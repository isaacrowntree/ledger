from abc import ABC, abstractmethod
from pathlib import Path

from etl.models import RawTransaction


class BaseParser(ABC):
    """Abstract base for all statement parsers."""

    @abstractmethod
    def parse(self, file_path: Path) -> list[RawTransaction]:
        """Parse a source file and return a list of raw transactions."""
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Return the source type identifier (e.g. 'ing', 'paypal', 'airbnb')."""
        ...
