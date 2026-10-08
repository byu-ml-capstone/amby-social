# Kanban Flow — Slice 1 follow-ups

Slice 1 is feature-complete on branch `slice-1`: 27 planned tasks implemented, each
independently reviewed, plus a final whole-branch review. Suite: **213 passed, 0 warnings**
(+1 benchmark deselected by default).

The final review returned **Ready with fixes**. Its Critical finding is **fixed** (commit
`19aac79`). The items below are the residual findings, recorded here because execution was
stopped deliberately at this point. Nothing below is speculative — every item was verified
against the running application by the reviewer.

## Fixed already

- **`patch_ticket` persisted JSON `null`.** Every `TicketUpdate` field is `| None`, and the
  handler blanket-`setattr`'d each set key, so an explicit `null` was *set* rather than omitted.
  `{"meta": null}` committed (JSON null serialises to `'null'`, satisfying `NOT NULL`), after
  which `GET /api/v1/projects/{slug}/tickets` returned **500 for every member of that project**
  — authenticated denial of service from one ordinary MEMBER call. Fixed by rejecting nulls for
  non-nullable fields before any mutation. Verified discriminating: neutralising the guard fails
  6 tests.

## Remaining — Important

1. **`name` is unvalidated on `POST /register` (HTML form path).** `app/routers/web.py`.
   No strip, no length check; a 10,000-character name and a whitespace-only name both persist.
   The JSON route enforces `min_length=1, max_length=50` via `RegisterRequest`; spec §4 says
   `User.name` is VARCHAR(50), which SQLite does not enforce. Fifth instance of "the web form
   path bypasses Pydantic".

2. **`POST /register` (HTML) has no `IntegrityError` guard.** The only check-then-insert in the
   codebase sitting in front of a unique constraint without the `rollback` → `409` treatment that
   `api_auth.register`, `services.create_project` and `api_projects.add_member` all have. Under
   concurrency the web path 500s where the JSON path 409s.

   *Both close with one change:* extract `services.register_user(session, *, name, email,
   password)` owning name validation, email normalisation, the duplicate pre-check and the
   `IntegrityError` guard; call it from both routers, each keeping its own presentation. This is
   the extraction `create_project` already received; registration is the only creation flow that
   never got it. The JSON route's existing tests passing unchanged is the evidence.

3. **`POST /logout` is guarded by neither CSRF nor the `Origin` check.** `app/routers/web.py`.
   D-11 requires the token on every HTML `POST`. `/login` and `/register` are legitimately exempt
   (no session exists yet to bind a token to); logout has one. `app/templates/base.html` already
   emits the `_csrf` field, so the form promises a check the route does not perform. Impact is
   forced logout, not compromise. One-line fix: `dependencies=[Depends(verify_csrf)]`.

4. **V-2's recorded evidence measures the API list query, not the board.**
   `tests/test_benchmark.py` times a `limit=50` select — the shape of
   `GET /api/v1/projects/{slug}/tickets`. The board route (`app/routers/web.py`) runs the same
   select **unbounded**, then renders every row through `render_markdown`. Measured at 5,000
   tickets: ~22 ms for the board's own query (over the 20 ms budget) and **~1.9 s / 8.2 MB of
   HTML** end to end. The recorded p95 of 0.35 ms is honest but describes a different query.
   Fix: cap tickets per column (200 matches the API's max), show a truncation indicator, and
   record both figures in the spec's V-2 row. Pagination controls are slice 2.

5. **The container `CMD` re-syncs at startup.** `Dockerfile` runs `uv run alembic … && uv run
   uvicorn …`. The image builds with `uv sync --frozen --no-dev`, but `uv run` defaults to
   syncing the project environment **including the dev group** — so first start reaches PyPI for
   `pytest` and `ruff` before the app boots. Slow with network, a hard failure without. Fix:
   `ENV PATH=/srv/.venv/bin:$PATH` and drop `uv run`, or use `uv run --no-sync --no-dev`.

## Remaining — worth doing cheaply

- `pyproject.toml` `authors` carries a personal email picked up from local git config by `uv init`.
- `app/routers/api_projects.py` re-exports `slugify` with `# noqa: F401` solely so two test files
  can import it from the old location. Update the imports, delete the shim.
- `README.md` tells the reader to set webhooks "in project settings" — there is no settings UI in
  slice 1; the only path is `PATCH /api/v1/projects/{slug}`.
- No startup guard on the placeholder `session_secret`. `app/config.py` defaults to
  `dev-insecure-secret-change-me` and `.env.example` ships `change-me-to-a-long-random-string` —
  both public strings that would let anyone forge a session cookie for any user id. Local-only
  today (D-09), but a startup assert when `secure_cookies` is true would stop it reaching slice 2.
- `load_ticket_for_read` / `load_ticket_for_write` are ~15 duplicated lines differing only in the
  final 404-vs-403. The *decision* was consolidated into `require_member`; the *lookup* was not.
- Front-end assets load from `cdn.tailwindcss.com` and `unpkg.com` with no SRI hashes, on pages
  carrying the session cookie. `cdn.tailwindcss.com` is dev-only per Tailwind's own docs. Vendor
  into `app/static/` (which spec §3 lists but was never created) by slice 2.

## Must be verified on a machine with Docker

Docker is not installed in the environment this was built in, so `docker compose up`, the image
build and the volume-durability proof were **never executed**. Everything the container wraps —
migrations, single-worker uvicorn, file-backed SQLite, `/health` — was verified natively.

1. `docker compose up --build` cold, with no `./data` and no `.env`; then `cp .env.example .env`
   and retry.
2. Watch first-start logs for `uv run` resolving packages (item 5 above). **Test with the
   daemon's network disabled** — that is the failure mode that bites a grader on conference wifi.
3. Confirm `DATABASE_URL: sqlite:////data/kanbanflow.db` (four slashes) reaches both alembic and
   the app, and the file lands on the host at `./data/kanbanflow.db`.
4. **SQLite WAL over a bind mount** — the highest-risk unverified item on macOS, where
   virtiofs/gRPC-FUSE is known to misbehave with WAL. Confirm `PRAGMA journal_mode` returns `wal`
   inside the container and that `-wal`/`-shm` files appear on the host.
5. `GET /health`, then the browser flow: register → create project → create ticket via the modal
   → change status via the dropdown. Note this needs internet for the CDN assets.
6. The durability claim the README makes: create a ticket, `docker compose down`,
   `docker compose up --build`, confirm it survives.
7. On a Linux host, whether `./data` ends up root-owned (no `user:` directive is set) and whether
   it can be removed without `sudo`.
