"""Parsers. `get_parser()` builds the real Docling parser on first use, once per process."""

from app.parsing.base import Parser

_parser: Parser | None = None


def get_parser() -> Parser:
    global _parser
    if _parser is None:
        # Imported here, not at the top: Docling pulls in torch, and only the worker image has it.
        from app.config import get_settings
        from app.parsing.docling_parser import DoclingParser

        _parser = DoclingParser(device=get_settings().parse_device)
    return _parser
