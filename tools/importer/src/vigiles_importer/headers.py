"""Mapping contractor column names to registry fields.

Matching ignores case, spacing, Polish diacritics and units in brackets,
so 'Średnica [mm]', 'srednica (mm)' and 'ŚREDNICA' all map to
``diameter_mm``. Unknown columns are ignored and reported in the log.
"""

import re
from dataclasses import dataclass

from vigiles_importer.csv_reader import FileFormatError
from vigiles_importer.text import fold

ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("nr elementu", "numer elementu", "nr", "numer", "kod", "kod elementu", "oznaczenie"),
    "asset_type": ("typ", "typ elementu", "rodzaj", "rodzaj elementu"),
    "building": ("budynek", "obiekt"),
    "floor": ("kondygnacja", "pietro", "poziom"),
    "room": ("pomieszczenie", "pom", "lokal"),
    "partition": ("przegroda", "rodzaj przegrody"),
    "installation": ("instalacja", "rodzaj instalacji", "medium"),
    "diameter_mm": ("srednica", "srednica mm", "fi"),
    "seal_system": ("system uszczelnienia", "system", "uszczelnienie"),
    "fire_rating": ("klasa ei", "klasa", "klasa odpornosci ogniowej", "odpornosc ogniowa", "ei"),
    "installed_at": ("data montazu", "data wykonania", "data"),
    "installer": ("brygada", "wykonawca", "monter"),
    "notes": ("uwagi", "notatki", "komentarz"),
}
REQUIRED = ("code", "asset_type", "building")

_LOOKUP = {alias: field for field, aliases in ALIASES.items() for alias in aliases}


def normalize(header: str) -> str:
    folded = fold(header.lstrip("﻿"))
    folded = re.sub(r"[\[(].*?[\])]", " ", folded)  # drop units: [mm], (mm)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def field_for(header: str) -> str | None:
    return _LOOKUP.get(normalize(header))


@dataclass(frozen=True)
class HeaderMap:
    columns: dict[str, int]  # field -> column index
    labels: dict[str, str]  # field -> header as written in the file
    ignored: list[str]


def map_headers(header: list[str]) -> HeaderMap:
    columns: dict[str, int] = {}
    labels: dict[str, str] = {}
    ignored: list[str] = []
    for index, name in enumerate(header):
        field = field_for(name)
        if field is None:
            if name:
                ignored.append(name)
            continue
        if field in columns:
            raise FileFormatError(
                f"columns {labels[field]!r} and {name!r} both map to {field}; keep one of them"
            )
        columns[field] = index
        labels[field] = name
    missing = [f for f in REQUIRED if f not in columns]
    if missing:
        expected = ", ".join(repr(ALIASES[f][0]) for f in missing)
        raise FileFormatError(f"missing required column(s): {expected}")
    return HeaderMap(columns=columns, labels=labels, ignored=ignored)
