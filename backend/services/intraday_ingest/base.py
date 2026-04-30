"""Format detection registry and abstract base handler."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, IO


class IntradayIngestError(Exception):
    """Base class for ingest errors."""


class UnknownFormatError(IntradayIngestError):
    """Raised when no handler matches the source file's header."""


class IntradayValidationError(IntradayIngestError):
    """Raised by validators when data fails schema or sanity checks."""


class BaseFormatHandler(ABC):
    HEADER_SIGNATURE: str = ""  # subclasses MUST override

    @abstractmethod
    def clean(self, source_path: str):
        """Read source_path, return a cleaned Polars DataFrame matching the
        intraday options Parquet schema."""


_REGISTRY: Dict[str, BaseFormatHandler] = {}


def register_handler(name: str, handler_cls) -> None:
    if not (isinstance(handler_cls, type) and issubclass(handler_cls, BaseFormatHandler)):
        raise TypeError(f"{handler_cls!r} must subclass BaseFormatHandler")
    _REGISTRY[name] = handler_cls()


def unregister_handler(name: str) -> None:
    _REGISTRY.pop(name, None)


def list_registered_formats():
    return sorted(_REGISTRY.keys())


def detect_format(stream: IO[str]) -> BaseFormatHandler:
    """Read the first line of `stream` and return a matching handler.
    The stream is consumed at least one line."""
    first_line = stream.readline().rstrip("\r\n")
    for handler in _REGISTRY.values():
        if first_line == handler.HEADER_SIGNATURE:
            return handler
    raise UnknownFormatError(
        f"No handler matches header: {first_line!r}. "
        f"Registered formats: {list_registered_formats()}"
    )
