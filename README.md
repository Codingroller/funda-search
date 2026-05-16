# Funda Search

A self-hosted web app that watches Funda.nl for new property listings matching your saved searches and sends push notifications via [ntfy](https://ntfy.sh).

Built on [pyfunda](https://github.com/0xMH/pyfunda) — the only working open-source client for Funda's mobile API.

> **License:** AGPL-3.0 (same as pyfunda)

## Features

- Save multiple named searches with full Funda filter support (location, price, area, rooms, energy label, property type, radius, …)
- Configurable polling interval per query (15 min → 24 h)
- Smart seeding on first save — no notification flood, only truly new listings trigger alerts
- Push notifications via self-hosted or public ntfy, with photo attachment and click-through to Funda
- Single-user password auth, HTMX-driven UI, zero JavaScript frameworks

## Stack

Python 3.12 · FastAPI · HTMX · SQLite · APScheduler · Docker

## Running locally

```bash
cp .env.example .env   # fill in ADMIN_PASSWORD and optionally NTFY_TOKEN
docker compose up --build
```

Open http://localhost:8000 — log in with your `ADMIN_PASSWORD`.

Or without Docker:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env   # edit as above
uvicorn app.main:app --reload
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | yes | 32+ byte random hex string for session signing |
| `ADMIN_PASSWORD` | yes | Login password (set once; change via Settings page) |
| `NTFY_BASE_URL` | yes | Your ntfy server, e.g. `https://ntfy.example.com` |
| `NTFY_TOKEN` | no | Bearer token if your ntfy server requires auth |
| `DB_PATH` | no | SQLite path (default `/data/funda.db`) |
| `TZ` | no | Timezone for scheduler display (default `Europe/Amsterdam`) |

## Deploying on Coolify

1. Fork / push this repo to GitHub
2. In Coolify → **New Resource → Application → Dockerfile**
3. Set the GitHub repo, branch `main`
4. Add a **Persistent Volume** mounted at `/data`
5. Set env vars (`SECRET_KEY`, `ADMIN_PASSWORD`, `NTFY_BASE_URL`, …)
6. Set domain and let Coolify provision Let's Encrypt
7. Deploy — first boot creates the DB and your user automatically

## Running tests

```bash
pytest
```

## Disclaimer

This project uses Funda's undocumented mobile API via pyfunda. Use responsibly, respect rate limits, and be aware this may violate Funda's Terms of Service. The API can change or break without notice.
