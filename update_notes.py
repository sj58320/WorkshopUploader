DEFAULT_UPDATE_NOTE = "Update asset"
UPDATE_NOTE_PLACEHOLDER = "Update asset..."


def normalize_update_note(value: str | None) -> str:
    normalized = (value or "").strip()
    return normalized or DEFAULT_UPDATE_NOTE
