"""Приведение имён специалистов к одному виду."""
from __future__ import annotations

import re


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


class SpecialistNormalizer:
    def __init__(self, specialists: dict[str, list[str]]):
        self._map: dict[str, str] = {}
        for canonical, aliases in specialists.items():
            canon = clean(canonical)
            self._map[canon.lower()] = canon
            for a in aliases:
                self._map[clean(a).lower()] = canon

    def __call__(self, raw: str | None) -> str:
        name = clean(raw)
        if not name:
            return "— не указан —"
        hit = self._map.get(name.lower())
        if hit:
            return hit
        # «Юлия К» / «Юлия С.» с точкой
        hit = self._map.get(name.rstrip(".").lower())
        return hit or name
