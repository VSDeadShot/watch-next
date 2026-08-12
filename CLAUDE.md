# CLAUDE.md

Guidance for Claude Code (and anyone else) working in this repository.

## What this is

**watch-next** answers one question — *what should I watch tonight?* — with one title,
chosen only from things the user can actually press play on right now.

Three product constraints hold the whole design up. None of them is a preference, and
none may be quietly relaxed to make a feature easier:

1. **One recommendation, never a list.** `RecommendationResponse.title` is a single
   object or `null`. There is no field anywhere that could hold a second answer, so no
   client can turn this into a feed. The constraint lives in the API contract, not the UI.
2. **Availability is a hard filter, not a ranking signal.** A title is a candidate only
   if it streams on a service the user pays for, or is free to everybody. It is applied
   before anything is scored and no weight can outvote it. Recommending something the
   user cannot watch is the single failure that makes the app worthless.
3. **No silent guessing.** A title the matcher cannot decide is stored as *unresolved*
   with its rejected candidates, not guessed at. A skipped import row is counted and
   reported, not dropped. Anything inferred says so.

Single user, no auth — but every table carries a `user_id` (constant `DEFAULT_USER_ID =
"local"`) from the first migration, so adding accounts later is config, not a rewrite.

## Stack

Python 3.12 (`requires-python >=3.11`), FastAPI, SQLAlchemy 2.0, Alembic, pydantic-settings,
pytest, ruff (line length 100, `select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]`).
`rapidfuzz` for title matching, `ijson` for the streaming YouTube parse,
`simple-justwatch-python-api` for the catalogue and availability.

Next.js 16 (App Router), React 19, TypeScript 5, Tailwind CSS 4, ESLint. No other runtime
dependencies — every component is hand-written, and there is no UI library.

## Layout

```
backend/          FastAPI + SQLAlchemy. Independent project, own pyproject.toml.
  app/
    core/         PURE. No FastAPI, no SQLAlchemy, no network, no clock.
    services/     IMPURE. Owns the session and the JustWatch client.
    api/          Routers. Thin: translate HTTP to a service call and back.
    models.py     ORM tables.  schemas.py  Pydantic request/response models.
    db.py         Engine, Base, UtcDateTime, get_db.   config.py  Settings.
  alembic/        Migrations. The schema's only owner.
  tests/          Mirrors the modules. fixtures/ is hand-written and anonymized.
  scripts/        smoke_justwatch.py — manual live check, NOT part of the suite.
frontend/         Next.js App Router + TypeScript + Tailwind 4. Independent project.
  app/            Routes.  components/  Flat, no subdirectories.
  lib/            api.ts (the only fetch), types.ts (mirrors the schemas),
                  urls.ts (mirrors core/urls.py), format.ts.
```

No root `package.json`. The two projects are built, tested and deployed separately.

## The one architectural rule: `core/` is pure

This is the most load-bearing decision in the repo. Everything genuinely interesting —
title parsing, normalization, fuzzy matching, taste profiling, mood weights, scoring, the
availability rule, the history counting, the streaming YouTube parser — lives in
`app/core/` and is testable with no database, no network and no mocking.

- `core/` takes data in and returns data out. It imports nothing from `app.services`,
  `app.api`, `app.models` or `app.db`. `now` is a parameter, never `datetime.now()`.
- `services/` is where the session and the HTTP client live. It fetches, persists, and
  calls into `core/` for every decision worth arguing about.
- `api/` translates HTTP into a service call. If a router grows a rule, the rule is in
  the wrong place.

When adding logic, the default question is *"can this be a pure function?"* — and it
usually can. `core/availability.py` decides whether an offer counts, knowing only short
names like `nfx`; `services/availability.py` supplies the three things that rule cannot
know on its own — the cached offers, the user's subscriptions, and what those services
are called.

## Commands

Always use the venv interpreter — **not** the system Python.

```bash
# Backend, from backend/
./.venv/Scripts/python.exe -m pytest                    # 945 tests, all offline
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/alembic.exe upgrade head                # before first run, and after pulling
./.venv/Scripts/uvicorn.exe app.main:app --reload       # http://localhost:8000

# Frontend, from frontend/
npx tsc --noEmit
npm run lint
npm run build
npm run dev                                             # http://localhost:3000
```

**Those three frontend checks are the entire list.** Prettier is *not* a project
dependency. Running `npx prettier` fetches a stray version from the registry and
reformats unrelated files — do not run it.

No test in the suite touches the network. `scripts/smoke_justwatch.py` does, deliberately,
and is run by hand because JustWatch is an unofficial API that can change without notice.

## Database

- One code path for SQLite (local) and Postgres (deployed); `DATABASE_URL` is the only
  difference. Write nothing backend-specific.
- **Alembic owns the schema.** The app never calls `create_all` — doing so would build
  tables Alembic's history knows nothing about, and the two would drift apart silently.
- `alembic.ini`'s `sqlalchemy.url` is commented out on purpose so no credentials can ever
  be committed; `alembic/env.py` reads it from `DATABASE_URL`.
- Migrations use `render_as_batch=True`, because SQLite rebuilds a table for any change.
- Every constraint is named by the convention in `db.py`. Without it, SQLite migrations
  fail outright with "Constraint must have a name".
- Timestamps use `UtcDateTime`, never a bare `DateTime`. It refuses naive datetimes on
  the way in and reattaches UTC on the way out, so SQLite and Postgres agree about what
  `watched_at` means.
- `tests/test_migrations.py` runs the migrations against a throwaway database and asks
  Alembic whether the models still match. A model changed without a migration passes
  every other test in the repo and then fails on deploy — this is what catches it.
- `tests/test_schema.py` compiles the tables against the Postgres dialect, because SQLite
  ignores `VARCHAR(n)` limits and Postgres rejects anything longer.

## Tables

| Table | Purpose |
|---|---|
| `imports` | One upload, with the counts the user was shown. |
| `watch_events` | Append-only. Raw exported title *and* the parsed reading of it. Unique on `(user_id, fingerprint)` — that constraint is what makes re-import idempotent. |
| `titles` | One catalogue row per distinct thing, as JustWatch describes it. |
| `title_resolutions` | "What is this string?", cached per normalized key + kind. Keeps the rejected candidates so the fixer UI can offer them. |
| `offers` | Availability cache, TTL'd via `fetched_at`. |
| `providers` / `user_providers` | The service catalogue per country, and what the user actually has. `user_providers` is deliberately not a foreign key — refreshing the catalogue must not delete somebody's settings. |
| `watchlist` | Stated intent, as opposed to everything else here, which is inferred. `watched_at` is how something leaves the list without being deleted. |
| `recommendations` | Every answer given, with its score and reasons — for the repeat cooldown, and so a recommendation can be argued with afterwards. |
| `discovery_runs` | Log of pool top-ups, so discovery is not re-run on every request. |
| `youtube_views` | Separate on purpose. YouTube is a taste and stats signal and **never** a recommendation candidate; a table that cannot join to the catalogue enforces that better than a convention. |

## API

```
POST   /api/imports/netflix           multipart; the raw Takeout .zip or a bare .csv
POST   /api/imports/youtube           multipart; watch-history.json, streamed with ijson
POST   /api/titles/resolve            resolve distinct titles against JustWatch; ?limit=
GET    /api/titles/unresolved         what the matcher declined, worst first; ?limit=&offset=
GET    /api/titles/search             free catalogue search; ?q= (>=2 chars), optional ?kind=
GET    /api/titles/resolutions        the recently decided-by-hand, newest first
PUT    /api/titles/resolutions/{id}   decide one by hand
GET    /api/providers                 the catalogue for the country
POST   /api/providers/refresh
GET    /api/providers/mine            PUT replaces the whole set in one request
POST   /api/offers/refresh            re-ask about titles whose availability went stale; ?limit=
POST   /api/recommend                 the product
GET    /api/watchlist                 POST adds; PATCH ticks off or edits a note; DELETE removes
GET    /api/stats                     what the history adds up to, and how much of it is unmatched
GET    /health
```

Watchlist routes are keyed by `title_id` rather than the row's own id — it is what a
caller already has, and there is at most one entry per title.

`resolve`'s `limit` counts **searches spent, not questions considered**. A question whose
answer is already stored is applied and skipped for free, and once the allowance runs out
the pass keeps going rather than stopping — so rows imported after their answer was
cached still get linked. It returns `remaining`, which is how a caller knows to ask for
another batch. **A batch whose every search failed does not reduce `remaining`**, so a
caller must stop on `failed == searched` too, or it will loop for as long as JustWatch is
down. `search` exists for the case the matcher could never have got right — a misspelled
export, a regional name — and runs only when asked, never per keystroke, because every
call is a real request against an API we pace at one a second by choice.

## Testing

Test-driven, and the tests are as much the deliverable as the code.

- **Write the test first, against a deliberately wrong stub**, so it fails on an
  assertion rather than an `ImportError`. A test that has never failed proves nothing.
- **Test names are sentences.** `test_a_null_note_clears_it`, not `test_patch_2`.
  Classes group by behaviour: `class TestWhereToWatchIt`.
- **A real in-memory SQLite database, not a mocked session.** Mocking proves the code
  calls SQLAlchemy; only a real database proves the unique constraint actually holds.
- **Table-driven cases** for the parsers — one row per real-world-shaped string.
- **Query counting** for anything that loops over rows. The `counting` fixture in
  `conftest.py` records statements via `before_cursor_execute`. Assert *constancy across
  list lengths* (3 rows versus 25), never a magic number — a magic bound breaks on any
  unrelated extra statement. A query per row is correct code that silently gets slower
  the more the app is used, and no assertion about the *answer* would ever catch it.
- **Mutation probes** once a suite is green: script a find/replace of a constant or a
  condition, run pytest, restore. A surviving mutation is a test gap unless you can argue
  it is genuinely equivalent. This has found real gaps more than once.
- **Over-fitting probes**: after green, run the edge cases nobody wrote a test for and
  read what actually happens.

API tests use `TestClient` with `get_db` and `get_settings` overridden in a fixture.

## Frontend

- **Monochrome, and colour is not a design axis.** Tokens live in `app/globals.css`:
  `ink` `#0a0a0a`, `panel` `#111111`, `raised` `#1a1a1a`, `line` `#2a2a2a`, `edge`
  `#3a3a3a`, `muted` `#a0a0a0`, `dim` `#8a8a8a`. Both greys clear WCAG AA on every
  surface they are used on, and nothing dimmer than `dim` exists. A poster is the only
  saturated thing on any screen.
- **`--text-display` is reserved for the recommendation card alone.** It is the one thing
  in this product allowed to shout. A display size spent on a settings heading is a size
  it can no longer spend on the answer.
- **`animate-reveal` is the one authored moment.** A reveal that happens on every element
  is not a reveal.
- **One way to talk to the backend**: `apiRequest()` in `lib/api.ts`. It separates
  `ApiError` (reached the server, got a failure) from `ApiUnreachableError` (the server
  is not running), because the fixes differ, and it reads FastAPI's `detail` in both its
  string and its validation-list shapes. Never call `fetch` directly.
- **`lib/types.ts` mirrors the Pydantic schemas by hand.** Change one, change the other.
- **`NEXT_PUBLIC_API_BASE_URL` is baked into the client bundle at build time.** Nothing
  secret may ever go in `frontend/.env.example` or `.env.local`.
- **Components are flat** in `components/`, with no subdirectories.
- **Six routes**: `/` (the answer), `/watchlist`, `/stats`, `/import`, `/settings` and
  `/resolve`. The first five are the nav; `/resolve` deliberately is not. It is a chore
  rather than a place — it exists while something is unmatched and never again once the
  queue is empty — so it is reached from the two screens that already know there is a
  problem, the import summary and the stats page.
- **The nav is two rows below `sm`, one row above it.** Four one-word labels beside the
  wordmark was the measured ceiling at 360px ("Your list" and "List" were both tried and
  overflowed), so the fifth link moved the labels onto their own line rather than behind a
  menu nobody would find. That lifts the ceiling instead of working around it, at a cost
  of about thirty pixels of sticky header on a phone. `components/Nav.tsx` records the
  reasoning and both measured underline offsets; read it before adding a route.
- Mobile is checked at 320 / 360 / 390 / 414px by embedding the page in an iframe of that
  width. `resize_window` is ignored by the Windows window manager and proves nothing.
- Only routes that exist are linked. A nav that points at a 404 to look finished is worse
  than a short nav.

## Writing style

Comments and docstrings here explain **why**, and are expected to be worth reading. A
comment restating the code is noise; a comment recording the decision behind it — the
alternative rejected, the failure it prevents, the constraint it honours — is what lets
the next person change the code safely. Match the density of the surrounding module.

Commit messages are prose rather than bullet lists, and say what changed and why it was
worth changing. Use `git commit -F <file>`; PowerShell here-strings mangle multi-line
`-m` into pathspecs.

## This repository is public

The input to this app is somebody's personal viewing history. From the first commit:

- **Never commit a real export.** `.gitignore` blocks `*.zip`, `ViewingActivity.csv`,
  `NetflixViewingHistory.csv`, `watch-history.json`, `watch-history.html`,
  `MyActivity.json` and `Takeout/`.
- **Test fixtures are hand-written and anonymized**, never a copy of a real export.
  Negated patterns keep `backend/tests/fixtures/**` tracked despite the rules above.
- `.env` and `*.db` are ignored; `.env.example` is committed.
- `EXPLAINER.md`, `watch-tracker-BRIEF.md`, `*-BRIEF.md`, `notes/` and `scratch/` are
  local-only and must never be pushed. Do not create scratch files inside the repo.

JustWatch data comes from an unofficial community wrapper whose terms permit
**non-commercial, personal use only**.

## Known limitations, left open deliberately

- A title of unknown runtime still passes a stated time budget (`fits(None)` is true, so
  that most series are not dropped), and the reasons do not admit the length is unknown.
- `recommender._pool`, the taste profile and `services/stats._sessions` all read their
  whole tables into memory — a sharp contrast with the YouTube importer, which holds
  nothing, and the watchlist, which batches its lookups. Worth fixing in all three at
  once rather than one at a time.
- A watch event whose title was never resolved cannot exclude anything from the pool. The
  fix is resolving the row, not guessing here.
