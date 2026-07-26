from __future__ import annotations

from typing import Final


DEFAULT_LANGUAGE: Final = "ko"
SUPPORTED_LANGUAGES: Final = ("ko", "en")
_language = DEFAULT_LANGUAGE


def normalize_language(value: str | None) -> str:
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def set_language(value: str | None) -> str:
    global _language
    _language = normalize_language(value)
    return _language


def get_language() -> str:
    return _language


def tr(korean: str, english: str, **values: object) -> str:
    text = english if _language == "en" else korean
    return text.format(**values) if values else text
