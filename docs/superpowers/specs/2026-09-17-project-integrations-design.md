# Project Integrations and GitHub Read-Only Sync Design

Date: 2026-09-17
Status: Approved in conversation; awaiting written-spec review

## 1. Purpose

Kanban Flow will provide one project-level web settings area for independent Slack,
Microsoft Teams, Discord, and GitHub connections. A project may enable all providers at
the same time. GitHub integration is read-only: it associates pull requests, commits,
reviews, and CI status with tickets and displays them in the centered ticket modal.

This design keeps the existing FastAPI, SQLModel, HTMX, and native HTML architecture.
No frontend framework or separate integration service is added.

## 2. Decisions

- Keep the centered ticket modal; do not reintroduce a slide-over drawer.
- Configure integrations per Kanban project in the existing Project Settings page.
- Allow Slack, Teams, Discord, and GitHub to be active simultaneously.
- Permit multiple GitHub repositories from one GitHub App installation/account per
  Kanban project.
- Give each project an immutable, globally unique uppercase key such as `PAY`.
- Match tickets through keys such as `PAY-104` in branch names, pull request titles or
  bodies, and commit messages.
- Require GitHub OAuth only from the project OWNER performing the connection. Other
  Kanban Flow users do not need GitHub accounts.
- Do not automatically change ticket status when a pull request merges.
- Process webhook deliveries synchronously after signature verification and database
  deduplication. A durable queue is deferred until observed volume requires one.

## 3. Scope

### Included

- Project key generation and read-only display.
- Independent chat webhook configuration for Slack, Teams, and Discord.
- GitHub App installation, OWNER OAuth verification, and repository selection.
- Multiple repositories from one GitHub installation per project.
- Initial synchronization of open pull requests when a repository is connected.
- GitHub webhook handling for installation repository changes, pull requests, pull
  request reviews, pushes, and check runs.
- Ticket association, development badges, and a development section in the centered
  ticket modal.
- Owner-only connection controls and member-visible connection status.
- Migration of existing single chat webhook settings.

### Excluded

- GitLab support in this phase.
- Multiple GitHub organizations/installations on one Kanban project.
- Creating branches, pull requests, commits, or checks from Kanban Flow.
- Automatic ticket transitions on merge.
- Historical import of all commits and merged pull requests.
- A durable message queue or periodic polling service.
- Mapping every Kanban user to a GitHub identity.
- The other proposed ticket enhancements—modal history synchronization, autosave,
  click-to-edit Markdown, checklists, labels, and a unified non-Git activity feed. They
  remain separate phases after integrations.

## 4. Data Model

### Project changes

Add `Project.key`, an immutable, non-null, globally unique uppercase identifier with a
maximum length of 10 characters. New keys are generated from the first project-name
token, preferring three alphanumeric characters (`Payment Gateway` becomes `PAY`). A
numeric suffix resolves collisions (`PAY2`). Existing projects receive keys through the
same deterministic migration routine.

### ProjectChatWebhook

- `id`: UUID primary key.
- `project_id`: indexed project foreign key.
- `provider`: `SLACK`, `TEAMS`, or `DISCORD`.
- `url`: webhook URL, maximum 500 characters.
- `created_at`, `updated_at`.
- Unique constraint on `(project_id, provider)`.

The existing `Project.webhook_type` and `Project.webhook_url` values migrate into this
table and are then removed. Dispatch sends each event independently to every configured
chat webhook. One failing destination cannot block another.

### GitHubInstallation

- `id`: UUID primary key.
- `github_installation_id`: unique integer from GitHub.
- `account_id`: GitHub account ID.
- `account_login`: display name of the user or organization.
- `connected_by_id`: Kanban Flow user who verified the installation.
- `created_at`, `updated_at`.

No GitHub user token or installation token is stored. Installation tokens are generated
on demand and expire naturally.

### ProjectGitHubRepository

- `id`: UUID primary key.
- `project_id`: indexed project foreign key.
- `installation_id`: GitHubInstallation foreign key.
- `github_repository_id`: indexed GitHub repository ID.
- `full_name`, `html_url`, `default_branch`.
- `active`, `disconnected_at`.
- `created_at`, `updated_at`.
- Unique constraint on `(project_id, github_repository_id)`.

All repository rows for a project must reference the same GitHub installation. The same
GitHub repository may be associated with another Kanban project because globally unique
project keys disambiguate ticket references.

### GitHubConnectState

- `id`: random nonce primary key, stored as a hash.
- `project_id`, `user_id`.
- `pending_installation_id`: untrusted value received from the setup callback.
- `expires_at`, `consumed_at`.

This record makes setup and OAuth state expiring and single-use. The signed browser state
contains only the nonce needed to find and validate the record.

### GitHubArtifact

Stores one normalized external object:

- `id`: UUID primary key.
- `repository_connection_id`: ProjectGitHubRepository foreign key.
- `kind`: `PULL_REQUEST` or `COMMIT`.
- `external_id`: GitHub node/database ID or commit SHA.
- `number`: pull request number when applicable.
- `title`, `html_url`, `author_login`.
- `state`: `DRAFT`, `OPEN`, `MERGED`, or `CLOSED` for pull requests.
- `review_state`: `REVIEW_REQUIRED`, `APPROVED`, or `CHANGES_REQUESTED` when applicable.
- `ci_state`: `PENDING`, `PASSED`, `FAILED`, or `NONE`.
- `head_sha`, `occurred_at`, `updated_at`.
- Unique constraint on `(repository_connection_id, kind, external_id)`.

### TicketGitLink

- `ticket_id`: ticket foreign key.
- `artifact_id`: GitHubArtifact foreign key.
- Unique constraint on `(ticket_id, artifact_id)`.

This association allows one pull request or commit to reference more than one ticket
without duplicating external state.

### IntegrationDelivery

- `provider`: initially `GITHUB`.
- `delivery_id`: provider delivery identifier.
- `event_type`, `received_at`.
- Unique constraint on `(provider, delivery_id)`.

The receiver inserts this row in the same transaction as event mutations. A failed
transaction remains retryable; a committed duplicate returns success without repeating
side effects.

## 5. Server Configuration and Authentication

The server receives these environment settings:

- `GITHUB_APP_ID`
- `GITHUB_APP_SLUG`
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `GITHUB_PRIVATE_KEY`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_API_URL`, defaulting to `https://api.github.com`
- `GITHUB_WEB_URL`, defaulting to `https://github.com`

`GITHUB_PRIVATE_KEY` accepts PEM content from an environment secret. The implementation
adds `PyJWT[crypto]` only; a full GitHub SDK is not needed.

The connect flow is:

1. An authenticated project OWNER starts the connection from Project Settings.
2. Kanban Flow creates a signed, expiring state containing project ID, OWNER ID, and a
   nonce, then redirects to the GitHub App installation URL.
3. GitHub returns to the setup URL with `installation_id`. Kanban Flow does not trust
   this query parameter.
4. Kanban Flow redirects the OWNER through GitHub App OAuth with another signed state.
5. The callback exchanges the authorization code for a GitHub user token and confirms
   that the installation is accessible to that user.
6. The user token is discarded immediately.
7. Kanban Flow authenticates as the GitHub App, creates a short-lived installation
   token, and lists repositories available to the installation.
8. The OWNER selects repositories and Kanban Flow persists the connection.

All state is signed with the existing application secret, expires after ten minutes,
and is bound to the current authenticated user and project. OAuth and setup callbacks
reject replayed, expired, mismatched, or non-owner state.

## 6. Project Settings UX

Add an `Integrations` section between Project Details and Members. It contains four
cards with consistent status, action placement, and explanatory text.

For an OWNER:

- Each chat card has `Connect` or `Replace`, plus a separate `Disconnect` action.
- A stored webhook URL is never rendered back into HTML. Connected cards show
  `Configured` and accept a new URL only when replacing it.
- The GitHub card has `Connect GitHub`, the connected account name, a multi-select list
  of accessible repositories, `Save repositories`, and `Disconnect`.
- Disconnect operations require CSRF and explicit confirmation.

For a MEMBER:

- Cards show only `Connected` or `Not connected` and selected repository names.
- Webhook URLs, GitHub identifiers, credentials, and mutation forms are absent.

Project Details displays the immutable project key and keeps project naming separate
from integrations.

## 7. GitHub Webhook Processing

Expose a cookie-independent endpoint such as `POST /integrations/github/webhook`.
Read the raw request body before JSON parsing and validate `X-Hub-Signature-256` with
HMAC-SHA256 and constant-time comparison. Reject missing or invalid signatures.

After validation:

1. Insert `X-GitHub-Delivery` into IntegrationDelivery.
2. Return `200` immediately for an already-recorded delivery.
3. Resolve affected ProjectGitHubRepository rows from the GitHub repository ID and
   installation ID.
4. Normalize the external artifact.
5. Extract ticket keys and reconcile TicketGitLink rows in one transaction.

Supported events:

- `pull_request`: create or refresh the pull request artifact and its ticket links.
- `pull_request_review`: refresh aggregate review state for the associated pull request.
- `push`: store only commits in the delivery that contain a recognized ticket key.
- `check_run`: refresh aggregate CI state for pull requests sharing the head SHA.
- `installation_repositories`: refresh repository availability and mark removed
  repository connections disconnected.
- `installation` with uninstall/suspend: disable affected repository connections while
  retaining ticket history.

Unknown actions and events return `200` without mutation after delivery recording.

## 8. Ticket Matching

Use a boundary-aware, case-insensitive pattern for the globally unique project key and
positive ticket number, for example `\bPAY-104\b`.

Inspect:

- pull request title;
- pull request body;
- pull request head branch;
- commit message from a push delivery.

Only tickets belonging to a project connected to the event repository may match. A
missing, deleted, or foreign ticket reference is ignored. Updating a pull request
reconciles its links, so removing a key removes the corresponding association. Commit
links are immutable once accepted.

## 9. Initial Synchronization and API Use

After repository selection, generate an installation token and fetch open pull requests
for each newly selected repository. Import only pull requests that reference a valid
ticket key. Do not import historical commits or closed pull requests.

Webhook handlers use payload data when it is sufficient. Review and CI aggregation may
make a targeted GitHub API request because a single review or check-run payload does not
represent the full aggregate state. HTTP calls have bounded timeouts; a failed refresh
keeps the last known state and logs a warning without damaging the ticket.

## 10. Centered Ticket Modal

Keep the approved 60/40 modal layout. Add:

- the immutable key (`PAY-104`) and copy button in the header;
- a generated branch command copy button using
  `git checkout -b feature/pay-104-<title-slug>`;
- compact PR and CI badges near the header when linked artifacts exist;
- a `Development` section below the ticket fields showing repository, PR/commit link,
  review state, CI state, author, and localized update time.

The comments panel remains on the right. Development links open GitHub in a new tab with
safe `rel` attributes. Clipboard actions use the existing vanilla JavaScript file and
provide visible success text for keyboard and assistive-technology users.

## 11. Authorization and Security

- Only a project OWNER may connect, replace, select, or disconnect integrations.
- Members may view non-secret integration status and linked artifacts.
- Chat webhook URLs never appear in member or owner HTML after storage and are redacted
  from project API responses.
- GitHub private keys, OAuth client secrets, webhook secrets, user tokens, and
  installation tokens are never stored in application tables or logs.
- Webhook signature verification happens before JSON parsing or database writes.
- OAuth state is signed, expiring, single-use, and user/project-bound.
- Installation and repository IDs from callbacks or forms are verified against GitHub;
  hidden form fields are not trusted.
- External titles, authors, repository names, and URLs are escaped and URL schemes are
  validated before rendering.

## 12. Migration and Deletion

The migration will:

1. Add and populate Project.key, then enforce uniqueness and non-nullability.
2. Create the integration and GitHub tables with foreign keys and indexes.
3. Copy each configured legacy chat webhook into ProjectChatWebhook.
4. Remove the legacy webhook columns only after the copy succeeds.

Deleting a project removes its chat webhooks, repository connections, artifacts, and
ticket links before deleting tickets. Disconnecting GitHub marks repository connections
inactive and hides their links while retaining normalized artifact history for audit;
hard project deletion removes it all. A GitHubInstallation row is removed only after no
project repository references it.

## 13. Error Handling

- A chat webhook update validates HTTPS, length, and destination safety using the existing
  SSRF-aware validation rules.
- One failed chat destination does not prevent other destinations from receiving an
  event.
- GitHub configuration missing on the server disables the Connect button and shows an
  owner-only setup message.
- OAuth cancellation or denied installation returns to Project Settings with a clear
  error and no partial connection.
- Repository synchronization failure keeps the verified installation and lets the OWNER
  retry selection.
- Removed or suspended GitHub installations show `Connection needs attention` without
  breaking ticket rendering.

## 14. Testing and Verification

- Model and migration tests for constraints, legacy chat migration, downgrade, and
  deletion order.
- Service tests for project-key generation, multi-provider chat dispatch, ticket-key
  parsing, link reconciliation, review/CI aggregation, and idempotent deliveries.
- Route tests for owner/member authorization, CSRF, secret redaction, OAuth state expiry
  and replay, callback installation verification, and repository selection validation.
- Webhook tests using signed raw payload fixtures for every supported event and invalid
  signatures.
- Formatter tests proving all configured chat destinations receive independent payloads.
- Template and browser tests for integration cards, multiple repository selection,
  ticket development badges, clipboard actions, modal accessibility, and mobile layout.
- All GitHub HTTP calls use deterministic fake transports; tests never require a live
  GitHub account.

## 15. Rollout Order

1. Project key and typed chat integration migration.
2. Unified Project Settings cards and multi-provider chat dispatch.
3. GitHub App authentication and multi-repository selection.
4. Webhook receiver, artifact normalization, and ticket matching.
5. Initial open-PR synchronization and modal development UI.

Each step leaves the application runnable and migratable. Existing chat notifications
continue working immediately after their data migration.
