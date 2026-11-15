"""Choosing the embedder."""
import pytest

from etl import embedding


@pytest.fixture(autouse=True)
def fresh():
    embedding.default_embedder.cache_clear()
    yield
    embedding.default_embedder.cache_clear()


def test_ngram_needs_no_model(monkeypatch):
    monkeypatch.setenv("LEDGER_EMBEDDER", "ngram")
    assert isinstance(embedding.default_embedder(), embedding.NgramEmbedder)


def test_unknown_choice_is_refused(monkeypatch):
    monkeypatch.setenv("LEDGER_EMBEDDER", "bge-huge")
    with pytest.raises(ValueError, match="bge-base, bge-small or ngram"):
        embedding.default_embedder()


def test_falls_back_down_the_list_when_a_model_will_not_load(monkeypatch):
    tried = []

    class Broken:
        MODELS = embedding.FastEmbedEmbedder.MODELS

        def __init__(self, size):
            tried.append(size)
            raise OSError("no network")

    monkeypatch.setenv("LEDGER_EMBEDDER", "bge-base")
    monkeypatch.setattr(embedding, "FastEmbedEmbedder", Broken)
    with pytest.warns(UserWarning):
        chosen = embedding.default_embedder()
    assert tried == ["bge-base", "bge-small"]
    assert isinstance(chosen, embedding.NgramEmbedder)


def test_vectors_are_unit_length():
    v = embedding.NgramEmbedder().embed(["WOOLWORTHS", ""])
    assert abs(float((v[0] ** 2).sum()) - 1) < 1e-5
    assert float((v[1] ** 2).sum()) == 0.0
