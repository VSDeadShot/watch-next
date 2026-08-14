# watch-next

A watch-history aggregator and availability-aware "what should I watch tonight" recommender.

Most trackers can tell you what you've seen. Almost none can tell you what you can actually
*press play on right now* — across the specific services you pay for. That's the gap this
fills, and it does it with a deliberate constraint: it recommends **one** title, not a
scrollable list, because a list of options is the problem, not the solution.

## Three constraints hold the design up

None of these is a preference, and none gets quietly relaxed to make a feature easier.

1. **One recommendation, never a list.** The response carries a single title or `null`.
   There is no field anywhere that could hold a second answer, so no client can turn this
   into a feed. The constraint lives in the API contract, not in the UI.
2. **Availability is a hard filter, not a ranking signal.** A title is a candidate only if
   it streams on a service you pay for, or is free to everybody. That is applied before
   anything is scored, and no weight can outvote it. Recommending something you cannot
   watch is the single failure that makes the app worthless.
3. **No silent guessing.** A title the matcher cannot decide is stored as *unresolved*,
   with its rejected candidates, rather than guessed at. A skipped import row is counted
   and reported, not dropped. Anything inferred says so.

## What it does

- **Imports real watch history.** Netflix and YouTube both let you export yours. Drop in
  the raw download — the `.zip` included — and it finds the right file and parses it. The
  YouTube history is streamed rather than loaded, because a real one does not fit in
  memory.
- **Matches what it can and admits what it can't.** Exported titles are strings, not
  identifiers: `"Kurukshetra: Dushasan"` has to become a catalogue entry. What the matcher
  is confident about is linked; what it isn't goes to a queue where you decide it by hand,
  with the candidates it weighed shown alongside. Decisions are cached by title, so
  re-importing never asks twice.
- **Knows what you subscribe to.** Pick your country and your actual services.
- **Filters by real availability**, and keeps that belief fresh — where a title streams
  changes, and nothing announces it, so answers older than a week can be re-asked.
- **Reveals one pick.** Tell it your mood and how much time you have; it gives you a single
  title and explains why it chose that one. Don't like it? Re-roll, and it won't come back.
- **Counts what you've watched.** How much, what genres, which decades, month by month —
  and how much of your history is still unmatched, because a number that quietly excludes
  things is worse than no number.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 (`requires-python >=3.11`), FastAPI, SQLAlchemy 2.0, Alembic |
| Database | SQLite locally, Postgres deployed — same code, via `DATABASE_URL` |
| Availability data | [`simple-justwatch-python-api`](https://github.com/Electronic-Mango/simple-justwatch-python-api) (JustWatch GraphQL) |
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4 |
| Recommendation | Rule-based scoring over a taste profile derived from your history |

Everything genuinely interesting — title parsing, fuzzy matching, the taste profile, mood
weights, scoring, the availability rule — lives in `backend/app/core/`, which imports no
FastAPI, no SQLAlchemy, no network and no clock. It is testable as plain functions. The
test suite is 1,275 tests and none of them touch the network.

## Local development

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev]"
cp .env.example .env

alembic upgrade head            # required: Alembic owns the schema, the app
                                # never creates tables itself
uvicorn app.main:app --reload   # http://localhost:8000

# Frontend, in another terminal
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

The frontend needs no configuration locally — it talks to `localhost:8000` by default.

Tests and linting, from `backend/` with the venv active:

```bash
pytest
ruff check .
ruff format --check .
```

`scripts/smoke_justwatch.py` does hit the network, deliberately, and is run by hand.
JustWatch is an unofficial API that can change without notice, and a test suite that fails
because somebody else deployed is a test suite people learn to ignore.

## Using it

Roughly in order, the first time:

1. **Import** your Netflix export, and your YouTube one if you want the stats.
2. **Resolve** — run the matcher over the library. It paces itself at one lookup a second
   by choice, so a large history takes a while; it runs in batches you can stop and resume.
   Whatever it declines is waiting for you on `/resolve`.
3. **Settings** — pick the services you actually pay for. Nothing gets recommended that you
   would have to subscribe to something new to watch, so this decides everything.
4. **Ask.** Mood, time available, film or series.

## Deploying it

The backend runs anywhere that can run a container or a Python process; the frontend is a
standard Next.js app. Four things are worth knowing:

- **Set `WATCH_NEXT_USER` and `WATCH_NEXT_PASSWORD`.** They are the Basic-auth credential
  that gates the whole site rather than just the API, and a production build with them
  unset answers **503 to everything** rather than serving your history openly. `next dev`
  ignores them, so a fresh checkout needs neither. There is no lockout behind this, so make
  the password long and random.
- **Set `API_SECRET`.** This app has one user and no login, which was the right shape for
  something that only ever answered on localhost. Deployed, that shape means whoever has
  the URL can read your viewing history, rewrite which services you pay for, and spend a
  rate-limited budget against somebody else's API. CORS does not help with any of that —
  it is a browser policy, and curl has never asked permission. With the secret set, the
  Next.js proxy holds it server-side and the browser never sees it. `/health` stays open,
  so platform health checks still work.
- **Run one backend process.** JustWatch calls are paced per process, so a second worker
  would double the request rate this project has chosen to keep.
- **Check what your host does to an upload.** On Vercel a request body over 4.5 MB is
  refused with a 413 before any of this app's code runs — measured, and upstream even of
  the password prompt above. The import page reads that ceiling from the platform and
  refuses an oversized file itself, with a message saying whose limit it is, because the
  alternative is the host's own error page. It matters most for the YouTube export: a real
  `watch-history.json` is far larger than that, and there is no smaller version of it to
  send. `CLAUDE.md` records the measurement and the two ways out.

Both `.env.example` files document every variable and why it exists.

## A note on the JustWatch data

Availability comes from an unofficial community wrapper around JustWatch's internal GraphQL
API. Its terms permit **non-commercial, personal use only**, which is exactly what this
project is. It would need a proper JustWatch data partnership before any commercial use,
and the API can change without notice — `scripts/smoke_justwatch.py` exists to check that
it still behaves.

## A note on privacy

The input to this app is somebody's personal viewing history, and this repository is
public. Real exports are gitignored by name, `.env` and `*.db` along with them, and every
test fixture is hand-written and anonymized rather than a copy of a real export. If you
fork this, keep it that way — a viewing history is more revealing than it looks.

## Status

Built and deployed, running against a real history. `CLAUDE.md` carries the architecture,
the conventions, and the limitations left open on purpose.
