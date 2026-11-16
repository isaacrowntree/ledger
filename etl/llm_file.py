"""`ledger categorise --llm`: Claude files what the categoriser couldn't.

Sends the pending rows (description, date, amount, source, the most similar
merchants already filed, receipt items when known) in batches to the Claude
API and files the answers by bucket, exactly like `--import`. This sends those
details to Anthropic, so it only runs when asked for. Needs the `llm` extra
(`pip install -e ".[llm]"`) and credentials (ANTHROPIC_API_KEY, or an
`ant auth login` profile).

An optional guide file (`--guide`, default config/categorising-guide.md if it
exists) carries the owner's conventions, e.g. "cafés are Coffee, meals are
Eating Out", and is sent with every batch.
"""
import json
from pathlib import Path

from etl.categorise_cmd import BUCKETS, INSTRUCTIONS, import_answers, pending_payload
from etl.categorizer import Categorizer

DEFAULT_MODEL = "claude-opus-5-5"
BATCH = 40
SYSTEM = (
    "You file personal and small-business bank transactions into the owner's own categories "
    "for an Australian personal-finance ledger. Follow the owner's conventions in the guide "
    "and the examples of how they filed similar merchants. Use only the listed categories. "
    "Be honest about uncertainty with the bucket: tax-relevant categories (Business:*, Rental:*, "
    "income) get 'unsure' unless the evidence is clear.")


def _schema(categories: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {"answers": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "category": {"type": "string", "enum": categories},
                "bucket": {"type": "string", "enum": list(BUCKETS)},
                "alternative": {"type": "string", "enum": categories + [""]},
                "note": {"type": "string"},
            },
            "required": ["id", "category", "bucket", "alternative", "note"],
            "additionalProperties": False}}},
        "required": ["answers"],
        "additionalProperties": False,
    }


def file_with_claude(conn, categorizer: Categorizer, *, model: str = DEFAULT_MODEL,
                     guide: Path | None = None, limit: int | None = None,
                     dry_run: bool = False, client=None) -> dict:
    """File the pending rows with Claude. Returns totals per bucket plus refused/failed batches."""
    payload = pending_payload(conn, categorizer, limit=limit)
    items = payload["items"]
    totals = {b: 0 for b in BUCKETS} | {"skipped": 0, "refused_batches": 0, "missing": 0}
    if not items:
        print("Nothing pending.")
        return totals
    if client is None:
        import anthropic           # the `llm` extra
        client = anthropic.Anthropic()
    guide_text = guide.read_text() if guide and guide.exists() else ""
    # The stable part goes first and is cached across batches.
    system = [{"type": "text", "text": "\n\n".join(filter(None, [
        SYSTEM, INSTRUCTIONS, f"Owner's conventions:\n{guide_text}" if guide_text else "",
        "Categories: " + json.dumps(payload["categories"]),
        "What each category holds: " + json.dumps(payload["category_examples"])])),
        "cache_control": {"type": "ephemeral"}}]
    schema = _schema(payload["categories"])
    print(f"Filing {len(items)} rows with {model} in batches of {BATCH} "
          "(descriptions, dates and amounts are sent to Anthropic)")
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",          # a safety decline is retried on a suitable model
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": "File these items:\n" + json.dumps(batch)}],
        )
        if response.stop_reason == "refusal":
            totals["refused_batches"] += 1
            print(f"  batch {start // BATCH + 1}: declined, left pending")
            continue
        if response.stop_reason == "max_tokens":
            totals["refused_batches"] += 1
            print(f"  batch {start // BATCH + 1}: answer cut off, left pending")
            continue
        text = next(b.text for b in response.content if b.type == "text")
        wanted = {it["id"] for it in batch}
        answers = {}
        for a in json.loads(text)["answers"]:
            if a["id"] in wanted:      # ignore anything not asked about
                answers[str(a["id"])] = {k: v for k, v in a.items() if k != "id" and v != ""}
        totals["missing"] += len(wanted) - len(answers)
        counts = import_answers(conn, categorizer, answers, dry_run)
        for k in counts:
            totals[k] = totals.get(k, 0) + counts[k]
    print(f"Done: {totals['sure']} sure, {totals['likely']} likely, {totals['unsure']} unsure, "
          f"{totals['missing']} unanswered, {totals['refused_batches']} batches left pending"
          + (" (dry run, nothing written)" if dry_run else ""))
    return totals
