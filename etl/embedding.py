"""Turn merchant keys into vectors for nearest-neighbour search.

Embedders share one interface:

- `FastEmbedEmbedder` runs a BGE model locally through ONNX (no GPU, no
  network after the first model download). It knows that "LAKSA" and
  "PHARMACY" are words, so it does better on merchants it has never seen.
  Two sizes, chosen with LEDGER_EMBEDDER:
    bge-small BAAI/bge-small-en-v1.5, 384 dims, ~67 MB (the default)
    bge-base  BAAI/bge-base-en-v1.5, 768 dims, ~210 MB, about 3x slower
  On the owner's ledger bge-base was no better overall and worse on recent
  rows (nearest-merchant guesses 82% right vs 93%), so small is the default.
- `NgramEmbedder` (LEDGER_EMBEDDER=ngram) hashes character n-grams. No model and
  fully deterministic, so the tests use it.

If a model cannot be loaded (fastembed missing, or no network for the first
download) the next one down is used: bge-base, then bge-small, then n-grams.
Changing embedder rebuilds the vector index on next use.
"""
import os
import warnings
import zlib
from functools import lru_cache
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    name: str
    dim: int
    # Cosine similarity above which a neighbour's category is trusted without
    # review. Scores are not comparable across embedders, so each sets its own.
    min_similarity: float

    def embed(self, texts: list[str]) -> np.ndarray:
        """Unit-length float32 rows, one per text."""
        ...


def _unit(v: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (v / norms).astype(np.float32)


class NgramEmbedder:
    def __init__(self, dim: int = 512, sizes: tuple[int, ...] = (2, 3, 4)):
        self.dim = dim
        self.sizes = sizes
        self.name = f"ngram-{dim}"
        self.min_similarity = 0.7

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in text.upper().split():
                padded = f" {word} "
                for n in self.sizes:
                    for j in range(len(padded) - n + 1):
                        out[i, zlib.crc32(padded[j:j + n].encode()) % self.dim] += 1.0
        return _unit(np.log1p(out))


class FastEmbedEmbedder:
    # name -> (model, dims, min_similarity). Cosine scores are not comparable
    # across models, so each has its own threshold, set where
    # `ledger categorise --evaluate` put guesses from the neighbours at about
    # 95% right on the owner's ledger.
    MODELS = {
        "bge-base": ("BAAI/bge-base-en-v1.5", 768, 0.85),
        "bge-small": ("BAAI/bge-small-en-v1.5", 384, 0.85),
    }

    def __init__(self, size: str = "bge-base"):
        from fastembed import TextEmbedding
        model, self.dim, self.min_similarity = self.MODELS[size]
        self._model = TextEmbedding(model)
        self.name = model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        # Title case reads more like the text the model was trained on.
        vecs = np.array(list(self._model.embed([t.title() for t in texts], batch_size=256)))
        return _unit(vecs)


@lru_cache(maxsize=1)
def default_embedder() -> Embedder:
    choice = os.environ.get("LEDGER_EMBEDDER", "bge-small")
    if choice == "ngram":
        return NgramEmbedder()
    if choice not in FastEmbedEmbedder.MODELS:
        raise ValueError(f"LEDGER_EMBEDDER={choice!r}: use bge-base, bge-small or ngram")
    order = list(FastEmbedEmbedder.MODELS)
    for size in order[order.index(choice):]:
        try:
            return FastEmbedEmbedder(size)
        except Exception as e:  # ImportError, or the first model download failed
            warnings.warn(f"embedder {size} unavailable ({e}); trying the next one down")
    return NgramEmbedder()
