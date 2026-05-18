"""Download all Dutch woonplaatsen (places) from the PDOK locatieserver and
write a sorted JSON list to static/dutch_cities.json.

Run from the project root:
    python scripts/download_cities.py

Re-run at any time to refresh the data.
Source: https://api.pdok.nl/bzk/locatieserver
"""
import json
import sys
from pathlib import Path

import httpx

URL = (
    "https://api.pdok.nl/bzk/locatieserver/search/v3_1/suggest"
    "?q=*&fq=type:woonplaats&rows=2502"
)
OUT = Path(__file__).parent.parent / "static" / "dutch_cities.json"


def main() -> None:
    print("Fetching Dutch city names from PDOK…")
    try:
        resp = httpx.get(URL, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        print(f"Error fetching data: {exc}", file=sys.stderr)
        sys.exit(1)

    docs = resp.json()["response"]["docs"]
    # Each doc's weergavenaam is "CityName, Municipality, Province".
    # Extract only the city name (the part before the first comma).
    names = sorted(set(doc["weergavenaam"].split(",")[0].strip() for doc in docs))

    OUT.write_text(json.dumps(names, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"Written {len(names)} city names to {OUT}")


if __name__ == "__main__":
    main()
