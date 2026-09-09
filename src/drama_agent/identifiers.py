"""Identifier validation shared by API and persistence layers."""
from __future__ import annotations

import re


SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_SAFE_IDENTIFIER_RE = re.compile(SAFE_IDENTIFIER_PATTERN)


def validate_identifier(value: str, label: str = "identifier") -> str:
    """Reject path separators and ambiguous identifiers before file access."""
    text = (value or "").strip()
    if not _SAFE_IDENTIFIER_RE.fullmatch(text):
        raise ValueError(
            f"{label} 只能包含字母、数字、下划线和连字符，且长度为 1~128"
        )
    return text
