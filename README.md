# Kanban Flow — Slice 1

Engineering workflow tracker: accounts, projects, membership, tickets, a Kanban
board, chat notifications on ticket create/done, and a JSON sprint API. Built
with FastAPI, SQLModel, and SQLite. Velocity, AI reports, GitHub webhooks, and
the MCP server are **not** part of this slice — see "Out of scope" below.

## Prerequisites

- Docker and Docker Compose (for the one-command run), **or**
- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (for local development)

## Run locally (Docker)

```bash
cp .env.example .env          # then edit SESSION_SECRET, see below
docker compose up --build
```

Open <http://localhost:8000> in a browser. Register an account, create a
project, and add a ticket to the board.

## Web workspace

The dashboard lists every project you can access. Open a project to reach its
active sprint Board; if there is no active sprint, Kanban Flow opens its
planning sprint, then Backlog as a fallback. Project navigation includes:

- **Board** — four sprint columns, filters, compact cards, and ticket details.
- **Backlog** — unassigned tickets and the planning sprint.
- **Sprint History** — closed-sprint summaries and close-time ticket states.
- **Settings** — project details, webhook configuration, and membership.

Only project **OWNER**s can create, start, or close sprints. Members can view
sprint pages and work with tickets, but cannot run sprint lifecycle actions.

On small screens, the project sidebar becomes a menu drawer. Board columns
remain full-width and scroll horizontally instead of being compressed.

The SQLite database file lives at `./data/kanbanflow.db` on the host, bind-mounted
into the container at `/data`. Rebuilding or recreating the container does not
delete this directory, so data survives `docker compose up --build`.

### Generate a real `SESSION_SECRET`

`.env.example` ships a placeholder, not a usable default. Before running,
generate your own value and put it in `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

For an internet-facing deployment, also set the production guardrails. The
application refuses to start if these values are insecure:

```dotenv
ENVIRONMENT=production
SECURE_COOKIES=true
ALLOWED_ORIGINS=["https://kanban.example.com"]
ALLOWED_HOSTS=["kanban.example.com"]
```

### Single worker, on purpose

The container always runs `uvicorn --workers 1`. SQLite allows exactly one
writer at a time; a second worker process reintroduces `database is locked`
errors under concurrent writes. Do not raise the worker count to improve
throughput — it will not, and it will break writes instead.

## Deploy on one EC2 instance

The production layout is one Dockerized application bound to
`127.0.0.1:8000`, with host Nginx as the only public entry point. Open ports
80/443 in the EC2 security group; restrict SSH to your IP or use AWS SSM.

1. Point the domain's DNS A record at the EC2 Elastic IP.
2. Copy `.env.example` to `.env` and set the production values shown above.
3. Start the application with `docker compose up -d --build`.
4. Replace `kanban.example.com` in `deploy/nginx/kanbanflow.conf`, then install
   it as `/etc/nginx/sites-available/kanbanflow` and enable the site.
5. Run `sudo nginx -t && sudo systemctl reload nginx`.
6. Install Certbot and run:

```bash
sudo certbot --nginx --redirect -d kanban.example.com
```

Certbot adds the HTTPS listener and HTTP-to-HTTPS redirect, and installs its
renewal timer. Verify renewal once with `sudo certbot renew --dry-run`.

The Nginx configuration overwrites forwarded-client headers and applies an
edge rate limit to authentication requests. Port 8000 is loopback-only, so the
application can safely trust the Nginx forwarded address for its own rate
limiter.

### Automatic deployment from `main`

The `Deploy to EC2` GitHub Actions workflow fast-forward pulls `main`, rebuilds
the container, runs migrations through the container startup command, and
waits for `/health`. Prepare `/srv/kanbanflow` on EC2 as a clone that can read
the repository, then add these secrets to the GitHub `production` environment:

- `EC2_HOST` — the EC2 domain or Elastic IP
- `EC2_USER` — the SSH user
- `EC2_SSH_KEY` — its private Ed25519 key
- `EC2_HOST_KEY` — the pinned `known_hosts` line from a trusted first setup

Protect the `production` environment if deployments require manual approval.
The workflow also supports a manual `workflow_dispatch` run.

## Develop (without Docker)

```bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --workers 1
```

## Tests

```bash
uv run pytest                      # unit and integration tests
uv run pytest -m benchmark -s      # V-2 board-query latency benchmark (deselected by default)
uv run ruff check . && uv run ruff format --check .
```

The benchmark carries the `benchmark` marker and is excluded from the default
run (`addopts = "-m 'not benchmark'"` in `pyproject.toml`), so it must be
requested explicitly with `-m benchmark` as shown above.

## Chat notifications

In project settings, set `webhook_type` to `SLACK`, `DISCORD`, or `TEAMS` and
paste the channel's incoming-webhook URL (https only). Cards are sent when a
ticket is created and when one moves to `DONE`. Delivery runs in the
background: a failed webhook is logged as a `WARNING` and never reverses the
ticket change.

## Gmail ticket intake

A project owner connects one Gmail mailbox and picks which Gmail labels create
tickets (each with a ticket type and priority). About every 90 seconds the app
reads new labeled mail and adds a backlog ticket: the subject becomes the title,
and the description holds the sender plus the body without quoted replies,
capped at 2000 characters. Replies in a thread that already has a ticket are
skipped. Real attachments come along with the ticket (see Ticket attachments).

One-time Google setup (needs a Workspace admin):

1. Create a GCP project under the company Workspace org and enable the Gmail API.
2. OAuth consent screen: type **Internal**. Scope: `gmail.readonly`.
3. Create a Web OAuth client with redirect URI
   `https://<host>/integrations/gmail/callback`.
4. Admin console → Security → API controls: mark the client **Trusted**.
5. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, and
   `TOKEN_ENCRYPTION_KEY` in `.env`, then restart.

If the mailbox password changes or access is revoked, the settings card shows
"Reconnect required". Reconnecting the same mailbox keeps the label mapping and
imports mail that arrived in the meantime. The poller runs inside the single
uvicorn worker. Don't add workers without moving it out.

## Ticket attachments

Drop files onto an open ticket, paste a screenshot with ⌘V, or use **Add files**. Images show as
thumbnails and open full size in a new tab; other files download. Mail imported by Gmail intake
brings its real attachments along (not inline signature images).

- Limits: 10 MB per file, 1 GB per project. Oversize mail attachments are listed in the ticket
  description instead of imported.
- Images over 2000 px on the long edge are downscaled on arrival and their EXIF (including GPS)
  is removed; the original is not kept. GIFs and animated images are stored as-is.
- Files are stored under `ATTACHMENTS_DIR` (`/data/attachments` in Docker), so back up `./data`
  as a whole. Nginx allows 25 MB request bodies for multi-file uploads.
- MCP: `get_ticket` lists attachments; `get_attachment` returns an image resized to 1568 px.

## Sprint API

Sprints are available through the JSON API. An `OWNER` creates a dated
`PLANNING` sprint, starts it with `PATCH /api/v1/sprints/{id}` and
`{"status": "ACTIVE"}`, then closes it with
`POST /api/v1/sprints/{id}/close` and a required `next_sprint_id` for an
existing `PLANNING` sprint. Closing snapshots the current sprint's tickets,
rolls unfinished tickets into that explicit next sprint, transitions the
current sprint to `CLOSED`, and leaves the next sprint in `PLANNING` until an
owner starts it.

The available commands are:

- `GET`/`POST /api/v1/projects/{slug}/sprints`
- `GET`/`PATCH /api/v1/sprints/{id}`
- `POST /api/v1/sprints/{id}/close`
- `GET /api/v1/sprints/{id}/history`

## MCP sprint tools

The local MCP server uses stdio and calls this application's API; it does not
open another network service. Start the web API first, then, while logged in,
issue a personal API token with `POST /api/v1/tokens` and a JSON body such as
`{"label":"local-mcp","scope":"read"}`. Tokens expire after 90 days and are
read-only by default; request `scope: "write"` only when mutation tools are
needed. The response's `token` value is shown exactly once;
copy it into a local `.env.mcp` file (start from `.env.mcp.example`) and never
commit the plaintext token.

For session-cookie token issue or revoke JSON requests, an `Origin` header,
when present, must be same-origin or allowed by the middleware. MCP calls use
bearer authentication instead: an invalid bearer token returns `401` and never
falls back to a session cookie.

The web app serves MCP over streamable HTTP at `/mcp`; clients send the token
as a bearer header, and the token's scope decides whether write tools work:

```bash
claude mcp add --transport http --scope user kanbanflow http://localhost:8000/mcp \
  --header "Authorization: Bearer paste-the-token-shown-once"
```

To run the server locally over stdio instead, use:

```dotenv
KANBANFLOW_BASE_URL=http://localhost:8000
KANBANFLOW_API_TOKEN=paste-the-token-shown-once
KANBANFLOW_READ_ONLY=true
```

MCP is read-only by default because ticket, comment, and GitHub text is
untrusted input to an AI client. Use a dedicated non-owner account for MCP.
Only set `KANBANFLOW_READ_ONLY=false` when write tools are needed and the MCP
client requires human approval before each write.

Configure your MCP client to launch the server as a stdio process, with those
three values in its environment:

```json
{
  "command": "uv",
  "args": ["run", "mcp", "run", "app/mcp_server.py:mcp", "--transport", "stdio"],
  "env": {
    "KANBANFLOW_BASE_URL": "http://localhost:8000",
    "KANBANFLOW_API_TOKEN": "paste-the-token-shown-once",
    "KANBANFLOW_READ_ONLY": "true"
  }
}
```

Equivalently, after loading those variables in your shell, run:

```bash
uv run mcp run app/mcp_server.py:mcp --transport stdio
```

The MCP tools cover:

- project create, list, read, rename, and delete
- ticket create, list, read, update, status change, and delete
- sprint list, create, start, close, read, update, and history
- project-member list, add, role update, and removal

Destructive tools retain the API's owner checks; sprint deletion is not
available. Revoke a lost or unused token with `DELETE /api/v1/tokens/{token_id}`;
deletion is idempotent and immediately prevents further bearer-token use.

## Out of scope for this slice

AI reports, GitHub webhooks, and rate limiting are out of scope.

## Design documents

- `cs482_slice1_design.md` — the authoritative spec for this slice
- `cs482_slice1_plan.md` — the implementation plan
- `cs482_workflow.md`, `cs482_tool_review.md` — original product research and specification
