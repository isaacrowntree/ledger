"""Turn merchant keys into vectors for nearest-neighbour search.

Two embedders share one interface:

- `FastEmbedEmbedder` runs BAAI/bge-small-en-v1.5 locally through ONNX (no
  GPU, no network after the first model download, about 130 MB). It knows that
  "LAKSA" and "PHARMACY" are words, so it does better on merchants it has never
  seen. This is the default.
- `NgramEmbedder` hashes character n-grams. No model and fully deterministic,
  so the tests use it. It is also the fallback when fastembed is not installed.

Set LEDGER_EMBEDDER=ngram to force the fallback.
"""
import os
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
    MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self):
        from fastembed import TextEmbedding
        self._model = TextEmbedding(self.MODEL)
        self.dim = 384
        self.name = self.MODEL
        self.min_similarity = 0.85

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        # Title case reads more like the text the model was trained on.
        vecs = np.array(list(self._model.embed([t.title() for t in texts], batch_size=256)))
        return _unit(vecs)


@lru_cache(maxsize=1)
def default_embedder() -> Embedder:
    if os.environ.get("LEDGER_EMBEDDER") == "ngram":
        return NgramEmbedder()
    try:
        return FastEmbedEmbedder()
    except ImportError:
        return NgramEmbedder()
