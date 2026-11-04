"""Text helpers shared by the CSV and JSON readers."""

import re
import unicodedata

# NFKD does not decompose these letters, so map them explicitly.
_SPECIAL = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ß": "ss"})


def fold(value: str) -> str:
    """Lowercase ASCII form used for matching: 'Średnica [mm]' -> 'srednica [mm]'."""
    decomposed = unicodedata.normalize("NFKD", value.translate(_SPECIAL))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def slug(value: str) -> str:
    """'Drzwi ppoż.' -> 'drzwi_ppoz'."""
    return re.sub(r"[^a-z0-9]+", "_", fold(value)).strip("_")


def blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
