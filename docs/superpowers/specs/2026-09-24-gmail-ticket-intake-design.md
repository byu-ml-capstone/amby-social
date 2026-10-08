# Gmail Ticket Intake Design

## Goal

Turn emails in the marketing team's shared Gmail account into Kanban Flow tickets automatically. The team already labels mail by hand; a label triggers a ticket.

**Scope of this spec: phase 1 only (label trigger, no Jev).** Phases 2–3 are recorded at the end for context and are not designed here.

## Context

- Mailbox: a real Google Workspace account on the company domain (not a Google Group), shared by the marketing team.
- Labels: applied manually by team members today. The label list and label → ticket mapping are **TBD** (to be provided); the design does not depend on specific labels.
- One connection per project; one mailbox per connection.
- Deployment: single EC2 nano instance, ~100 users. Load is per connection (about one to two Gmail API calls per minute), not per user.

## Google Setup (one-time, needs Workspace admin)

- GCP project created **under the company Workspace org**, Gmail API enabled.
- OAuth consent screen type **Internal** → no Google verification, no 100-user cap, no 7-day refresh-token expiry.
- Web OAuth client, redirect URI `https://<host>/integrations/gmail/callback`.
- Admin console → API controls: mark the OAuth client **Trusted**.
- Settings: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `TOKEN_ENCRYPTION_KEY` (Fernet; `cryptography` declared as a direct dependency).
- `GOOGLE_REDIRECT_URI` is explicit because Google requires an exact match, and deriving it from the request gives `http://` behind Nginx. Production requires it to be `https://`.

Domain-wide delegation is rejected: it grants access to every mailbox in the domain.

## Components

Mirrors the existing GitHub integration.

| File | Role | Modeled on |
|---|---|---|
| `app/gmail.py` | httpx client: token refresh, `labels.list`, `getProfile`, `history.list`, `messages.list/get`, revoke | `app/github.py` |
| `app/gmail_parse.py` | Pure function `parse_message(payload) -> ParsedEmail(title, body, sender, attachment_count)`; no network, no DB | new |
| `app/gmail_sync.py` | Poll loop, per-connection sync, calls parser and `services.create_ticket()` | `app/github_sync.py` |
| `app/routers/gmail.py` | Connect, callback, disconnect, save label mapping | `app/routers/github_webhook.py`, `web.py` |
| `app/models.py` + alembic | `GmailConnection` table | — |
| `app/config.py` | `google_client_id`, `google_client_secret`, `token_encryption_key` | GitHub settings |
| `project_settings.html` | Connect button, label mapping form, status | GitHub section |

No Google SDK; `httpx` only.

## Connect Flow

1. Project settings → "Connect Gmail" → Google authorize URL: scope `gmail.readonly`, `access_type=offline`, `prompt=consent`, `state` signed with `itsdangerous` (bound to session + project id).
2. Callback exchanges the code for tokens; refresh token stored encrypted.
3. `users.getProfile` → store the mailbox address and current `historyId`. Mail from before the connection is not imported.
4. `labels.list` → owner maps labels to ticket defaults (type, priority). Only user labels (`type == "user"`) are offered, not system labels such as INBOX.

Someone with the shared account's credentials connects once.

Reconnecting (after `needs_reauth`):
- **Same mailbox:** keeps `history_id` and the label mapping, so mail that arrived while access was broken is imported on the next cycle.
- **Different mailbox:** resets both, because label ids and history ids belong to the old mailbox.

## Polling

Background asyncio task started in the FastAPI lifespan (`app/main.py`), cancelled on shutdown. Runs every 90 seconds. Started only when all four Google settings are configured.

- Each cycle runs entirely in `asyncio.to_thread` using the sync `httpx` client and its own `Session`, so neither Gmail calls nor SQLite writes block the event loop. This matches the `GitHubClient` style and keeps tests synchronous.
- Access tokens are cached in memory per connection until shortly before expiry.
- Each connection is wrapped in its own `try/except`; one failing connection does not stop others or later cycles.
- `# ponytail:` The Dockerfile runs `--workers 1`. With more workers the loop would run once per worker; the dedupe constraint below still prevents duplicate tickets, but a separate process or lock would be needed.

### One cycle (per connection)

1. Refresh the access token (cached in memory, refreshed shortly before expiry).
2. `history.list(startHistoryId, historyTypes=[messageAdded, labelAdded])`; keep messages carrying a **mapped** label.
3. For each message: `messages.get(format=full)` → dedupe → `parse_message` → `create_ticket()` → record delivery.
4. When all messages are handled, save the new `historyId` and `last_synced_at`.
5. `history.list` returns 404 (history expired) → fall back to `messages.list(q="label:<X> newer_than:2d")`. Dedupe makes the replay safe.

### Dedupe and threads

- Message dedupe: `IntegrationDelivery(provider="GMAIL", delivery_id="<project_id>:<Gmail message id>")`. The Gmail `id` is used instead of the `Message-ID` header, which can be missing or duplicated. Provider value is uppercase to match the existing `"GITHUB"` convention.
- Thread replies: together with the ticket, also record `IntegrationDelivery(provider="GMAIL_THREAD", delivery_id="<project_id>:<threadId>")`. A message whose thread already has a row is skipped. No schema change and no JSON querying of `meta`.
- Keys carry the project id so that two projects connected to the same mailbox each get their own ticket instead of sharing dedupe rows.
- A label removed before the cycle fetches the message: no ticket (the fetched message's current labels decide).
- A message carrying several mapped labels produces **one** ticket, using the label processed first.

### `historyId` advancement

- Transient failure (5xx, 429, network): `historyId` is **not** advanced. The next cycle replays the batch; dedupe prevents duplicates.
- Permanent per-message failure (`create_ticket()` raises `HTTPException` on validation, or the parser raises on a malformed payload): skip that message, record `last_error`, advance. One bad mail must not block the queue. `create_ticket()` raises `HTTPException` because it was written for request handlers, so the sync code must catch it.

## Email Parsing (`gmail_parse.py`)

Kept deliberately minimal. Only labeled mail is fetched, so volume is low; the concern is the size of each ticket description, which reaches LLM clients through MCP. `DESCRIPTION_MAX_LENGTH` is 20,000, too large for mail, so email has its own cap.

- Body: prefer `text/plain`. If absent, extract text from HTML with stdlib `html.parser`, dropping the contents of `<style>`, `<script>`, `<head>`. (`bleach.clean(strip=True)` keeps CSS text, so it is not used for this.)
- Remove quoted replies: everything from an `On ... wrote:` line, and lines starting with `>`.
- Collapse runs of blank lines and whitespace.
- Cap at `EMAIL_BODY_MAX_CHARS = 2000` (starting value, tune with real samples); append `…(truncated, N chars omitted)`.
- If parsing yields an empty body, fall back to the start of the raw text.
- Attachments: only an "N attachments" line; attachment content is never fetched.

Not built until real samples show a need: footer/signature pattern stripping, URL shortening, Gmail `fields` response trimming.

## Email → Ticket Mapping

| Ticket field | Source |
|---|---|
| title | Subject without `Re:`/`Fwd:`, truncated to `TITLE_MAX_LENGTH`; empty → `(no subject) from <sender>` |
| description | `From: <sender>` line + parsed body |
| type / priority | Label mapping; default `TASK` / `MEDIUM` |
| sprint | None (backlog) |
| creator | User who connected Gmail |
| meta | `{source: "gmail", from, message_id, thread_id}`; confirm it passes `validate_meta` |

The full original body is not stored; the Gmail message id in `meta` allows opening it in Gmail.

## Data Model

New table `gmail_connection`:

`id`, `project_id` (unique), `user_id`, `google_email`, `refresh_token_enc`, `label_mapping` (JSON: label_id → {name, type, priority}), `history_id`, `status` (`ACTIVE` / `NEEDS_REAUTH`, uppercase like every other enum in `app/models.py`), `last_synced_at`, `last_error`, `created_at`.

Project delete removes the connection.

Dedupe reuses `IntegrationDelivery` (unique on `provider` + `delivery_id`).

## Failure Handling

| Situation | Behavior |
|---|---|
| `invalid_grant` (password change, revoked access) | `status=NEEDS_REAUTH`, polling for that connection stops, settings card shows "Reconnect required" plus the error, `WARNING` logged |
| 5xx / 429 / network | Record `last_error`, do not advance `historyId`, retry next cycle |
| Per-message validation or parse failure | Skip that message, record `last_error`, advance |
| Disconnect | Call Google revoke endpoint (best effort), delete the connection row |

No per-user notification on reauth: `app/notifications.py` only delivers project chat webhooks, and there is no per-user channel. Tickets created from mail do fire the normal "New ticket" chat notification, like web-created tickets.

Route order: the Gmail router is included before `web_sprints`, whose `/settings/integrations/{provider}/disconnect` route would otherwise capture `gmail`.

## Security

- Email content is untrusted; UI rendering goes through the existing sanitizer (`app/rendering.py`).
- Refresh token encrypted at rest (Fernet) and never logged. In production, missing `TOKEN_ENCRYPTION_KEY` fails startup (add to the existing production checks in `config.py`).
- OAuth `state` signed and bound to session + project.
- Scope is `gmail.readonly` only.
- Mail content can reach MCP tool output, a prompt-injection surface for LLM clients. Noted, not mitigated beyond parsing and the length cap.

## Testing

- `parse_message`: unit tests on payload fixtures (plain-only, HTML-only, quoted reply, no subject, oversize body).
- Sync cycle with a fake Gmail client: ticket creation, dedupe, thread reply skipped, `historyId` held on transient error, 404 fallback, `invalid_grant` → `needs_reauth`.
- Router: `state` validation on callback, disconnect. Follow existing `tests/test_web_*` style.
- No automated calls to real Gmail; one manual check after admin setup.

## Later Phases (not designed here)

2. **Jev shadow mode.** Jev predicts a label for each new inbox message; results are logged only and compared with manual labels to measure accuracy.
3. **Jev auto-create.** High-confidence predictions create tickets; low confidence falls back to manual labeling; manual labels always win.

Both require company approval to send mail content to TypeSafe. Open: TypeSafe request/response schema (early access), free-tier rate limit, data retention policy, early-access account.

## Open Items

- Label list and label → ticket mapping (marketing team).
- Workspace admin: GCP project under the org, trusted OAuth client.
- 3–5 redacted sample emails for parser fixtures and tuning `EMAIL_BODY_MAX_CHARS`.

Resolved: MCP `list_tickets` returns a 120-character summary, but `get_ticket` returns the full description, so the email body cap still matters.

## Out of Scope

Attachments, writing labels back to Gmail (`gmail.modify`), Pub/Sub push, multiple mailboxes per project, thread replies as comments, Jev integration.
