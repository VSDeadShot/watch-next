# watch-next

A watch-history aggregator and availability-aware "what should I watch tonight"
recommender.

Most trackers can tell you what you've seen. Almost none can tell you what you can
actually *press play on right now* — across the specific services you pay for. That's the
gap this fills, and it does it with a deliberate constraint: it recommends **one** title,
not a scrollable list, because a list of options is the problem, not the solution.

## What it does

- **Imports real watch history.** Netflix and YouTube both let you export yours. Drop in
  the raw download — the `.zip` included — and the app finds the right file and parses it.
- **Knows what you subscribe to.** Pick your country and your actual services.
- **Filters by real availability.** Titles you can't stream on a service you pay for never
  get recommended. This is a hard filter, not a ranking signal.
- **Reveals one pick.** Tell it your mood and how much time you have; it gives you a single
  title and explains why it chose it. Don't like it? Re-roll one replacement.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy |
| Database | SQLite locally, Postgres in production (same code, via `DATABASE_URL`) |
| Availability data | [`simple-justwatch-python-api`](https://github.com/Electronic-Mango/simple-justwatch-python-api) (JustWatch GraphQL) |
| Frontend | Next.js (App Router), TypeScript, Tailwind CSS |
| Recommendation | Rule-based scoring over a taste profile derived from your history |

## Status

Early development. See `CLAUDE.md` for architecture and conventions.

## A note on the JustWatch data

Availability comes from an unofficial community wrapper around JustWatch's internal
GraphQL API. Its terms permit **non-commercial, personal use only**, which is exactly what
this project is. It would need a proper JustWatch data partnership before any commercial
use, and the API can change without notice — `scripts/smoke_justwatch.py` exists to check
that it still behaves.

## Local development

```bash
# Backend
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload                     # http://localhost:8000

# Frontend
cd frontend
npm install
npm run dev                                       # http://localhost:3000
```

Tests: `cd backend && pytest`
