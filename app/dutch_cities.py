"""In-memory Dutch city name list loaded from static/dutch_cities.json.

The list is populated by scripts/download_cities.py from the PDOK locatieserver
and committed to the repo. It is loaded once at import time (~150 KB RAM).
"""
import json
from pathlib import Path

_path = Path(__file__).parent.parent / "static" / "dutch_cities.json"
_CITIES: list[str] = json.loads(_path.read_text(encoding="utf-8")) if _path.exists() else []


def search_cities(q: str, limit: int = 10) -> list[str]:
    """Return up to `limit` city names matching `q` (prefix matches first)."""
    q_lower = q.lower().strip()
    if not q_lower:
        return []
    prefix = [c for c in _CITIES if c.lower().startswith(q_lower)]
    if len(prefix) >= limit:
        return prefix[:limit]
    seen = set(prefix)
    rest = [c for c in _CITIES if q_lower in c.lower() and c not in seen]
    return (prefix + rest)[:limit]
