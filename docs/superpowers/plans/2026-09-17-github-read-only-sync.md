# GitHub Read-Only Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect one GitHub App installation to a project, select multiple repositories, ingest read-only pull-request and commit activity, and show linked development state on tickets.

**Architecture:** Build directly on the completed Integration Foundation plan: `Project.key`, typed chat integrations, and the Integrations section in `project_settings.html` must already exist. Keep GitHub HTTP/authentication in `app/github.py`, persistence and matching in `app/github_sync.py`, browser connection routes beside the existing settings routes in `app/routers/web_sprints.py`, and the unauthenticated signed webhook route in `app/routers/github_webhook.py`. Store normalized artifacts and links; never store GitHub access tokens.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Alembic, httpx, itsdangerous, PyJWT with crypto, Jinja2, vanilla JavaScript, pytest

**Spec:** `docs/superpowers/specs/2026-09-17-project-integrations-design.md`

**Depends on:** `docs/superpowers/plans/2026-09-17-integration-foundation.md` implemented and passing in full.

## Global Constraints

- Run this plan only after the foundation migration is the repository's single Alembic head.
- Support one GitHub installation and multiple selected repositories per project.
- GitHub is read-only: do not create branches, commits, pull requests, reviews, or checks.
- Only a project OWNER may start connection, finish OAuth, select repositories, retry sync, or disconnect GitHub.
- MEMBERs may view connection status and linked development artifacts, never connection controls or secrets.
- Never store or log GitHub OAuth user tokens or installation tokens; generate installation tokens on demand.
- OAuth state lasts ten minutes and is signed, single-use, bound to the current user and project, and backed by a hashed nonce row.
- Verify `X-Hub-Signature-256` against the raw body before JSON parsing or database access.
- Use bounded httpx timeouts and deterministic `httpx.MockTransport` tests; no test uses a live GitHub account.
- Initial sync imports only open pull requests that reference an existing ticket in the connected project.
- A pull-request update reconciles ticket links; accepted commit links are immutable.
- Do not change ticket status after a pull request merges.
- Keep the centered ticket modal's 60/40 layout and right-side comments panel.
- Do not add a GitHub SDK or a frontend framework.

---

### Task 1: GitHub Persistence, Configuration, and Deletion Order

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `app/config.py`
- Modify: `app/models.py`
- Modify: `app/services.py`
- Create: `alembic/versions/42f6c8e1ad30_add_github_integration.py`
- Create: `tests/test_github_models.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**

- Consumes: `Project.key: str` from the Integration Foundation plan.
- Produces: `GitHubArtifactKind`, `GitHubArtifactState`, `GitHubReviewState`, and `GitHubCIState` string enums.
- Produces: `GitHubInstallation`, `ProjectGitHubConnection`, `ProjectGitHubRepository`, `GitHubConnectState`, `GitHubArtifact`, `TicketGitLink`, and `IntegrationDelivery` SQLModel tables.
- Produces settings fields `github_app_id`, `github_app_slug`, `github_client_id`, `github_client_secret`, `github_private_key`, `github_webhook_secret`, `github_api_url`, and `github_web_url`.

- [ ] **Step 1: Prove the foundation is the single passing migration head**

Run:

```bash
test "$(uv run alembic heads | wc -l | tr -d ' ')" = "1"
uv run pytest -q tests/test_project_keys.py tests/test_chat_integrations.py tests/test_web_project_settings.py tests/test_migrations.py
```

Expected: both commands exit 0. Stop execution of this plan if either fails.

- [ ] **Step 2: Add failing model, constraint, and deletion tests**

Create `tests/test_github_models.py` with these assertions:

```python
def test_project_repository_is_unique_per_project(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    installation = GitHubInstallation(
        github_installation_id=7001,
        account_id=91,
        account_login="acme",
        connected_by_id=owner.id,
    )
    session.add(installation)
    session.flush()
    session.add(ProjectGitHubRepository(
        project_id=project.id,
        installation_id=installation.id,
        github_repository_id=501,
        full_name="acme/api",
        html_url="https://github.com/acme/api",
        default_branch="main",
    ))
    session.commit()
    session.add(ProjectGitHubRepository(
        project_id=project.id,
        installation_id=installation.id,
        github_repository_id=501,
        full_name="acme/api",
        html_url="https://github.com/acme/api",
        default_branch="main",
    ))
    with pytest.raises(IntegrityError):
        session.commit()
```

Add these runnable constraint/race/deletion tests in the same file:

```python
def test_competing_project_installation_bindings_allow_exactly_one(
    engine, session, make_user, make_project
):
    owner = make_user(email="binding-owner@example.com")
    project = make_project(owner, name="Binding Race")
    first = GitHubInstallation(
        github_installation_id=7001, account_id=1, account_login="one",
        connected_by_id=owner.id,
    )
    second = GitHubInstallation(
        github_installation_id=7002, account_id=2, account_login="two",
        connected_by_id=owner.id,
    )
    session.add_all([first, second])
    session.commit()
    first_id, second_id = first.id, second.id
    with Session(engine) as left, Session(engine) as right:
        assert left.get(ProjectGitHubConnection, project.id) is None
        assert right.get(ProjectGitHubConnection, project.id) is None
        left.add(ProjectGitHubConnection(project_id=project.id, installation_id=first_id))
        right.add(ProjectGitHubConnection(project_id=project.id, installation_id=second_id))
        left.commit()
        with pytest.raises(IntegrityError):
            right.commit()
    stored = session.get(ProjectGitHubConnection, project.id)
    assert stored.installation_id in {first_id, second_id}

def test_delivery_identity_is_provider_scoped(session):
    session.add(IntegrationDelivery(
        provider="GITHUB", delivery_id="delivery-1", event_type="push"
    ))
    session.commit()
    session.add(IntegrationDelivery(
        provider="GITHUB", delivery_id="delivery-1", event_type="pull_request"
    ))
    with pytest.raises(IntegrityError):
        session.commit()

def test_delete_project_removes_state_binding_and_repositories(
    session, make_user, make_project
):
    owner = make_user(email="delete-github@example.com")
    project = make_project(owner, name="Delete GitHub")
    installation = GitHubInstallation(
        github_installation_id=7003, account_id=3, account_login="delete",
        connected_by_id=owner.id,
    )
    session.add(installation)
    session.flush()
    session.add(ProjectGitHubConnection(
        project_id=project.id, installation_id=installation.id
    ))
    session.add(GitHubConnectState(
        id="a" * 64, project_id=project.id, user_id=owner.id,
        expires_at=utcnow() + timedelta(minutes=10),
    ))
    session.commit()
    delete_project(session, project, project.slug)
    assert session.get(ProjectGitHubConnection, project.id) is None
    assert session.get(GitHubConnectState, "a" * 64) is None
```

- [ ] **Step 3: Run model tests and verify the missing-table failure**

Run: `uv run pytest -q tests/test_github_models.py`

Expected: collection fails because the GitHub model classes do not exist.

- [ ] **Step 4: Add the one required dependency and exact environment settings**

Run:

```bash
uv add 'PyJWT[crypto]>=2.10.1,<3'
```

Add these `Settings` fields in `app/config.py`:

```python
github_app_id: str | None = None
github_app_slug: str | None = None
github_client_id: str | None = None
github_client_secret: str | None = None
github_private_key: str | None = None
github_webhook_secret: str | None = None
github_api_url: str = "https://api.github.com"
github_web_url: str = "https://github.com"
```

- [ ] **Step 5: Add the exact enums and table shapes**

Add to `app/models.py`:

```python
class GitHubArtifactKind(StrEnum):
    PULL_REQUEST = "PULL_REQUEST"
    COMMIT = "COMMIT"

class GitHubArtifactState(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"

class GitHubReviewState(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"

class GitHubCIState(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NONE = "NONE"

```

Define the tables with these exact columns and constraints:

```text
GitHubInstallation: id PK; github_installation_id unique int; account_id int;
account_login varchar(255); connected_by_id FK user.id; created_at; updated_at.

ProjectGitHubRepository: id PK; project_id indexed FK project.id; installation_id
indexed FK github_installation.id; github_repository_id indexed int; full_name
varchar(255); html_url varchar(500); default_branch varchar(255); active bool default
true; disconnected_at nullable datetime; created_at; updated_at; unique
(project_id, github_repository_id); composite FK (project_id, installation_id) to
project_github_connection(project_id, installation_id).

ProjectGitHubConnection: project_id PK/FK project.id; installation_id indexed FK
github_installation.id; created_at; updated_at; unique (project_id, installation_id).

GitHubConnectState: id varchar(64) PK containing SHA-256(nonce); project_id indexed FK
project.id; user_id indexed FK user.id; pending_installation_id nullable int;
expires_at; consumed_at nullable datetime; created_at.

GitHubArtifact: id PK; repository_connection_id indexed FK
project_github_repository.id; kind; external_id varchar(255); number nullable int;
title varchar(500); html_url varchar(500); author_login varchar(255); state nullable;
review_state nullable; ci_state; head_sha nullable varchar(64); occurred_at; updated_at; unique
(repository_connection_id, kind, external_id).

TicketGitLink: ticket_id FK ticket.id and artifact_id FK github_artifact.id as a
composite primary key; created_at.

IntegrationDelivery: id PK; provider varchar(20), stored as the literal `GITHUB`;
delivery_id varchar(255); event_type
varchar(100); received_at; unique (provider, delivery_id).
```

- [ ] **Step 6: Create the concrete migration from the implemented foundation head**

Run:

```bash
test "$(uv run alembic heads | awk 'NR == 1 {print $1}')" = "e4b9c52d8fa1"
uv run alembic revision --rev-id 42f6c8e1ad30 --head e4b9c52d8fa1 -m "add github integration"
```

Edit the generated migration to create the seven tables, foreign keys, indexes, enum-compatible varchar columns, and unique constraints listed in Step 5. Create `project_github_connection` before `project_github_repository`; the latter uses `sa.ForeignKeyConstraint(["project_id", "installation_id"], ["project_github_connection.project_id", "project_github_connection.installation_id"])`. Downgrade drops them in this order: `ticket_git_link`, `github_artifact`, `integration_delivery`, `project_github_repository`, `project_github_connection`, `github_connect_state`, `github_installation`.

- [ ] **Step 7: Update hard project deletion in dependency order**

Before deleting tickets in `delete_project()`, select the project's repository connection IDs and installation IDs, then delete `GitHubConnectState`, `TicketGitLink`, `GitHubArtifact`, `ProjectGitHubRepository`, and `ProjectGitHubConnection` in that order. After the project is gone, delete each `GitHubInstallation` only when no `ProjectGitHubConnection.installation_id` references it. Keep the existing chat webhook deletion from the foundation plan.

- [ ] **Step 8: Add migration assertions and run the slice**

Extend `tests/test_migrations.py` to assert all seven tables, every unique constraint and the composite repository-to-project-connection foreign key from Step 5, the repository/artifact/delivery indexes, upgrade → downgrade one revision → upgrade, and rejection of duplicate repository, artifact, link, delivery, installation, and project binding rows.

Run:

```bash
uv run pytest -q tests/test_github_models.py tests/test_migrations.py
uv run ruff check app/config.py app/models.py app/services.py tests/test_github_models.py tests/test_migrations.py
```

Expected: all tests pass and Ruff exits 0.

- [ ] **Step 9: Commit the persistence slice**

```bash
git add pyproject.toml uv.lock app/config.py app/models.py app/services.py alembic/versions/42f6c8e1ad30_add_github_integration.py tests/test_github_models.py tests/test_migrations.py
git commit -m "feat: add GitHub integration persistence"
```

---

### Task 2: GitHub App Authentication, Expiring State, and HTTP Client

**Files:**

- Create: `app/github.py`
- Create: `tests/test_github_client.py`
- Create: `tests/test_github_state.py`

**Interfaces:**

- Produces: `AvailableRepository(id: int, full_name: str, html_url: str, default_branch: str)` frozen dataclass.
- Produces: `github_is_configured(config: Settings = settings) -> bool`.
- Produces: `issue_github_state(session: Session, project_id: str, user_id: str, pending_installation_id: int | None = None, now: datetime | None = None) -> str`.
- Produces: `read_github_state(session: Session, signed_state: str, user_id: str, now: datetime | None = None) -> GitHubConnectState`, which validates without consuming and is used only by repository-selection GET.
- Produces: `consume_github_state(session: Session, signed_state: str, user_id: str, now: datetime | None = None) -> GitHubConnectState`.
- Produces: `GitHubClient(config: Settings = settings, client: httpx.Client | None = None)` with `exchange_code(code: str) -> str`, `verify_user_installation(user_token: str, installation_id: int) -> None`, `installation(installation_id: int) -> dict`, `repositories(installation_id: int) -> list[AvailableRepository]`, `open_pull_requests(installation_id: int, full_name: str) -> list[dict]`, `pull_request_reviews(installation_id: int, full_name: str, number: int) -> list[dict]`, `check_runs(installation_id: int, full_name: str, sha: str) -> list[dict]`, and `pull_requests_for_commit(installation_id: int, full_name: str, sha: str) -> list[dict]`. Installation tokens remain private to the client.

- [ ] **Step 1: Write failing state tests**

Create `tests/test_github_state.py` proving a state round-trip returns its project and pending installation, only a SHA-256 digest is stored as `GitHubConnectState.id`, and consumption rejects a changed signature, wrong user, expired row, already-consumed row, deleted project, or user who is no longer an OWNER. Use a fixed aware UTC `datetime` and advance it by 601 seconds for expiry.

```python
signed = issue_github_state(session, project.id, owner.id, now=now)
row = session.exec(select(GitHubConnectState)).one()
assert len(row.id) == 64
assert signed not in row.id
assert read_github_state(session, signed, owner.id, now=now).consumed_at is None
assert consume_github_state(session, signed, owner.id, now=now).project_id == project.id
assert session.get(GitHubConnectState, row.id).consumed_at == now
```

- [ ] **Step 2: Write failing deterministic client tests**

Create `tests/test_github_client.py` with `httpx.MockTransport`. Assert:

```text
exchange_code: POST /login/oauth/access_token with client_id, client_secret, code and
Accept: application/json; reject HTTP errors, malformed JSON, a 200 JSON error, and a
response without a non-empty access_token;
verify_user_installation: GET /user/installations/7001/repositories using bearer
`ghu_test_user_token`;
installation: GET /app/installations/7001 using an RS256 App JWT bearer token;
repositories(7001): privately POST /app/installations/7001/access_tokens, then GET
/installation/repositories and follow every Link rel="next" page;
open_pull_requests(7001, "acme/api"): get a private installation token, then GET
/repos/acme/api/pulls?state=open&per_page=100 and follow every next page;
pull_request_reviews(7001, "acme/api", 17): get a private installation token, then GET
/repos/acme/api/pulls/17/reviews?per_page=100 and follow every next page;
check_runs(7001, "acme/api", "deadbeef"): get a private installation token, then GET
/repos/acme/api/commits/deadbeef/check-runs?per_page=100 and follow every next page;
pull_requests_for_commit(7001, "acme/api", "deadbeef"): get a private installation
token, then GET /repos/acme/api/commits/deadbeef/pulls?per_page=100 and follow every
next page.
```

Add this exact hostile-pagination test; the authorization-bearing second request must never occur:

```python
def test_pagination_rejects_next_link_outside_api_origin(configured_settings):
    requests = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "api.github.com"
        if request.url.path == "/app/installations/7001/access_tokens":
            return httpx.Response(201, json={"token": "ghs_test_installation_token"})
        return httpx.Response(
            200,
            json=[],
            headers={"Link": '<https://evil.example/repos?page=2>; rel="next"'},
        )
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        github = GitHubClient(configured_settings, transport)
        with pytest.raises(ValueError, match="pagination left configured API origin"):
            github.open_pull_requests(7001, "acme/api")
    assert len(requests) == 2  # installation-token request plus first list page only
    assert all(request.url.host == "api.github.com" for request in requests)
```

Also assert a ten-second timeout, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, percent-encoded path components, non-2xx `raise_for_status()`, and that token strings never occur in captured logs.

- [ ] **Step 3: Run both files and verify missing-module failure**

Run: `uv run pytest -q tests/test_github_state.py tests/test_github_client.py`

Expected: collection fails because `app.github` does not exist.

- [ ] **Step 4: Implement signed state with stdlib hashing and itsdangerous**

Use `secrets.token_urlsafe(32)`, `hashlib.sha256`, and `URLSafeTimedSerializer(settings.session_secret, salt="kf-github-connect")`. Sign only `{"nonce": nonce}`. Store only the digest plus row bindings. Put all signature, digest, row, user, expiry, project, and OWNER checks in `_validated_github_state`; `read_github_state` returns that row without mutation, while `consume_github_state` claims it with a conditional update and commit:

```python
claim = session.exec(
    update(GitHubConnectState)
    .where(GitHubConnectState.id == row.id, GitHubConnectState.consumed_at.is_(None))
    .values(consumed_at=now)
)
if claim.rowcount != 1:
    session.rollback()
    raise HTTPException(400, "GitHub state already used")
session.commit()
return session.get(GitHubConnectState, row.id)
```

Every malformed/expired/replayed rejection is HTTP 400; lost ownership is HTTP 403 and a deleted project is HTTP 404.

- [ ] **Step 5: Implement the minimal GitHub HTTP client**

Use PyJWT RS256 with claims `iat=now-60`, `exp=now+540`, and `iss=github_app_id`. Parse the PEM directly from `github_private_key`. Keep `_installation_token(installation_id)` private and have every installation list method obtain its own token. Use one `_paginated(path, headers, params, item_key=None)` helper for repositories, pull requests, reviews, check runs, and commit-to-PR lookup:

```python
def _paginated(self, path, headers, params, item_key=None):
    items = []
    url, query = path, params
    while url:
        absolute = urljoin(self._config.github_api_url, url)
        expected = urlsplit(self._config.github_api_url)
        actual = urlsplit(absolute)
        if (actual.scheme, actual.hostname, actual.port) != (
            expected.scheme, expected.hostname, expected.port
        ):
            raise ValueError("GitHub pagination left configured API origin")
        response = self._client.get(absolute, headers=headers, params=query, timeout=10.0)
        response.raise_for_status()
        body = response.json()
        items.extend(body[item_key] if item_key else body)
        url = response.links.get("next", {}).get("url")
        query = None
    return items
```

`exchange_code` calls `response.raise_for_status()`, catches `ValueError` from `response.json()` as `GitHub OAuth returned invalid JSON`, rejects `body.get("error")`, and requires `isinstance(body.get("access_token"), str) and body["access_token"]`. Return only the typed repository dataclass and raw dicts needed by sync. Close only an internally-created `httpx.Client`; never close an injected test client. Do not log headers or response bodies.

- [ ] **Step 6: Run and commit the authentication slice**

Run:

```bash
uv run pytest -q tests/test_github_state.py tests/test_github_client.py
uv run ruff check app/github.py tests/test_github_state.py tests/test_github_client.py
```

Expected: all tests pass and Ruff exits 0.

```bash
git add app/github.py tests/test_github_state.py tests/test_github_client.py
git commit -m "feat: add GitHub App authentication client"
```

---

### Task 3: OWNER Connection Flow and Repository Selection

**Files:**

- Create: `app/github_sync.py`
- Modify: `app/routers/web_sprints.py`
- Modify: `app/templates/project_settings.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_web_project_settings.py`

**Interfaces:**

- Produces routes: `POST /projects/{slug}/settings/integrations/github/connect`, `GET /integrations/github/setup`, `GET /integrations/github/callback`, `GET /projects/{slug}/settings/integrations/github/repositories`, `POST /projects/{slug}/settings/integrations/github/repositories`, and `POST /projects/{slug}/settings/integrations/github/disconnect`.
- Produces: `save_project_repositories(session: Session, project: Project, installation: GitHubInstallation, repositories: list[AvailableRepository]) -> list[ProjectGitHubRepository]`.
- Produces: `disconnect_project_github(session: Session, project: Project) -> None`.
- Consumes: Task 2 state and client interfaces and the foundation's `_settings()`, `project_settings.html`, and `settings_world` test fixture.

- [ ] **Step 1: Write and run failing Connect authorization/configuration tests**

Add these tests to `tests/test_web_project_settings.py`:

```python
def test_owner_starts_github_connect(client, settings_world, login_as, configured_github):
    login_as(settings_world.owner.email)
    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/connect",
        data=_csrf(settings_world.owner),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "https://github.com/apps/kanban-flow/installations/new?state="
    )

def test_member_and_missing_csrf_cannot_start_github_connect(
    client, settings_world, login_as, configured_github
):
    url = f"/projects/{settings_world.project.slug}/settings/integrations/github/connect"
    login_as(settings_world.member.email)
    assert client.post(url, data=_csrf(settings_world.member)).status_code == 403
    login_as(settings_world.owner.email)
    assert client.post(url, data={}).status_code == 403
```

Also assert an unconfigured OWNER page contains a disabled Connect button and `GitHub App settings are not configured`, while the MEMBER page contains neither the setup text nor a GitHub form.

Run: `uv run pytest -q tests/test_web_project_settings.py::test_owner_starts_github_connect tests/test_web_project_settings.py::test_member_and_missing_csrf_cannot_start_github_connect`

Expected: FAIL because the connect route does not exist.

- [ ] **Step 2: Implement only the Connect POST and make Step 1 green**

Add the route to `app/routers/web_sprints.py` beside the foundation integration routes:

```python
@router.post(
    "/projects/{slug}/settings/integrations/github/connect",
    dependencies=[Depends(verify_csrf)],
)
def start_github_connect(
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    if not github_is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub App is not configured")
    state = issue_github_state(session, project.id, user.id)
    query = urlencode({"state": state})
    return RedirectResponse(
        f"{settings.github_web_url}/apps/{settings.github_app_slug}/installations/new?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
```

Run the two Step 1 node IDs again. Expected: PASS.

- [ ] **Step 3: Write and run failing setup/OAuth callback lifecycle tests**

Add tests that issue real signed states and inject a MockTransport-backed `GitHubClient`. Assert setup consumes its state once, rejects missing/invalid `installation_id`, and redirects with a fresh OAuth state. Assert callback consumes that OAuth state, rejects `error=access_denied`, missing code, replay, expiry, wrong user, and user-installation verification failure without a `GitHubInstallation` row. Assert success persists account ID/login and redirects with a third, unconsumed selection state.

```python
setup = client.get(
    f"/integrations/github/setup?installation_id=7001&state={quote(setup_state)}",
    follow_redirects=False,
)
assert setup.status_code == 303
oauth_state = parse_qs(urlparse(setup.headers["location"]).query)["state"][0]
callback = client.get(
    f"/integrations/github/callback?code=oauth-code&state={quote(oauth_state)}",
    follow_redirects=False,
)
assert callback.status_code == 303
selection_state = parse_qs(urlparse(callback.headers["location"]).query)["state"][0]
assert read_github_state(session, selection_state, settings_world.owner.id).consumed_at is None
```

Run: `uv run pytest -q tests/test_web_project_settings.py -k 'github_setup or github_oauth_callback'`

Expected: FAIL because both callback routes are absent.

- [ ] **Step 4: Implement setup and OAuth callback, then make Step 3 green**

Both GET callbacks call `consume_github_state` before redirecting or reporting cancellation. Setup treats `installation_id` as untrusted and only carries it in the new state. OAuth calls `exchange_code`, then `verify_user_installation`; only after verification does it call `installation`, validate integer `id`/`account.id` and non-empty `account.login`, and upsert `GitHubInstallation`. It then issues a new selection state and redirects to:

```python
query = urlencode({"state": selection_state})
location = f"/projects/{project.slug}/settings/integrations/github/repositories?{query}"
```

Map cancellation to `?github_error=installation_cancelled`, OAuth denial to `?github_error=oauth_denied`, and upstream HTTP/response failures to `?github_error=verification_failed`; never include GitHub's raw error text or a token. Run the Step 3 command again. Expected: PASS.

- [ ] **Step 5: Write and run failing repository GET/POST state tests**

Assert GET validates but does not consume the selection state, renders only server-returned repositories 501 and 502, and includes the same signed state in a hidden field. POST is CSRF-protected, consumes the state once, re-fetches availability, rejects ID 999 with no repository rows, and ignores submitted names/URLs/branches. Assert a different installation is rejected even when every prior row is inactive.

```python
page = client.get(
    f"/projects/{project.slug}/settings/integrations/github/repositories?state={quote(state)}"
)
assert page.status_code == 200
assert 'value="501"' in page.text and 'value="502"' in page.text
assert read_github_state(session, state, owner.id).consumed_at is None

saved = client.post(
    f"/projects/{project.slug}/settings/integrations/github/repositories",
    data={"state": state, "repository_ids": ["501", "502"], **_csrf(owner)},
    follow_redirects=False,
)
assert saved.status_code == 303
with pytest.raises(HTTPException, match="already uses another GitHub installation"):
    save_project_repositories(session, project, second_installation, [other_repository])
```

Run: `uv run pytest -q tests/test_web_project_settings.py -k 'github_repository_selection'`

Expected: FAIL because selection GET/POST and persistence services are absent.

- [ ] **Step 6: Implement verified repository GET/POST and make Step 5 green**

The GET route calls `read_github_state`, verifies the state project equals the slug project, loads the `GitHubInstallation` by `pending_installation_id`, calls `GitHubClient.repositories(installation.github_installation_id)`, and renders `project_settings.html` through `_settings()` with `github_selection_state` and `github_available_repositories`. The POST route calls `consume_github_state` first, repeats the same project/installation checks, fetches repositories again, parses submitted decimal IDs, and passes only matching `AvailableRepository` values to the service.

Claim the database-enforced project binding before any repository mutation. A stale read may race, so catch the binding unique constraint, roll back, reload, and reject a different installation:

```python
binding = session.get(ProjectGitHubConnection, project.id)
if binding is None:
    binding = ProjectGitHubConnection(
        project_id=project.id, installation_id=installation.id
    )
    session.add(binding)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        binding = session.get(ProjectGitHubConnection, project.id)
else:
    session.refresh(binding)
if binding is not None and binding.installation_id != installation.id:
    raise HTTPException(409, "Project already uses another GitHub installation")
```

The `ProjectGitHubRepository` composite foreign key is the final guard that every repository uses the bound installation. Upsert selected server metadata, reactivate selected rows, and timestamp omitted rows. Commit repository selection before returning; initial synchronization is Task 6 and no Retry control is rendered in this task. Run the Step 5 command and the competing-binding test from Task 1 again. Expected: PASS.

- [ ] **Step 7: Write and run exact disconnect/attention tests**

```python
def test_github_disconnect_requires_owner_csrf_and_confirmation(
    client, settings_world, login_as, connected_github
):
    url = f"/projects/{settings_world.project.slug}/settings/integrations/github/disconnect"
    login_as(settings_world.member.email)
    assert client.post(
        url, data={"confirm": "Disconnect GitHub", **_csrf(settings_world.member)}
    ).status_code == 403
    login_as(settings_world.owner.email)
    assert client.post(url, data={"confirm": "Disconnect GitHub"}).status_code == 403
    assert client.post(
        url, data={"confirm": "wrong", **_csrf(settings_world.owner)}
    ).status_code == 422
    assert connected_github.active is True
    response = client.post(
        url,
        data={"confirm": "Disconnect GitHub", **_csrf(settings_world.owner)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    session.refresh(connected_github)
    assert connected_github.active is False
    assert connected_github.disconnected_at is not None
```

Also assert `active=False, disconnected_at=None` renders `Connection needs attention`, whereas an OWNER-disconnected row renders `Disconnected`. Run: `uv run pytest -q tests/test_web_project_settings.py -k 'github_disconnect or github_attention'`. Expected: FAIL before route/card implementation.

- [ ] **Step 8: Implement disconnect and the GitHub card, then make Step 7 green**

Add an OWNER-only, CSRF-protected disconnect route that requires the exact confirmation string. `disconnect_project_github` timestamps every project repository row made inactive and commits. Extend the existing Integrations section in `app/templates/project_settings.html`; do not introduce a partial absent from the foundation. Show selected repository controls only during selection GET, active repository names to MEMBERs, and OWNER Connect/Disconnect controls. Do not render Retry until Task 6. Reserve `active=False, disconnected_at=None` for webhook-reported attention.

- [ ] **Step 9: Run and commit the complete connection slice**

```bash
uv run pytest -q tests/test_web_project_settings.py tests/test_csrf.py tests/test_github_state.py tests/test_github_client.py
uv run ruff check app/github_sync.py app/routers/web_sprints.py tests/test_web_project_settings.py
git add app/github_sync.py app/routers/web_sprints.py app/templates/project_settings.html app/static/app.css tests/test_web_project_settings.py
git commit -m "feat: connect GitHub repositories"
```

---

### Task 4: Ticket Matching, Artifact Normalization, and Aggregation

**Files:**

- Modify: `app/github_sync.py`
- Create: `tests/test_github_sync.py`

**Interfaces:**

- Produces: `extract_ticket_numbers(project_key: str, texts: Iterable[str | None]) -> set[int]`.
- Produces: `aggregate_review_state(reviews: Sequence[dict]) -> GitHubReviewState`.
- Produces: `aggregate_ci_state(check_runs: Sequence[dict]) -> GitHubCIState`.
- Produces: `upsert_pull_request(session: Session, connection: ProjectGitHubRepository, pull: dict, config: Settings = settings) -> GitHubArtifact`.
- Produces: `reconcile_pull_request_links(session: Session, connection: ProjectGitHubRepository, artifact: GitHubArtifact, texts: Iterable[str | None]) -> None`.
- Produces: `insert_linked_commit(session: Session, connection: ProjectGitHubRepository, commit: dict, config: Settings = settings) -> GitHubArtifact | None`.

- [ ] **Step 1: Write failing pure-function tests**

Add this fixture and pure-function tests to `tests/test_github_sync.py`:

```python
@pytest.fixture
def github_sync_world(session, make_user, make_project):
    owner = make_user(email="sync-owner@example.com")
    project = make_project(owner, name="Payment Gateway")
    installation = GitHubInstallation(
        github_installation_id=7001, account_id=91, account_login="acme",
        connected_by_id=owner.id,
    )
    session.add(installation)
    session.flush()
    session.add(ProjectGitHubConnection(
        project_id=project.id, installation_id=installation.id
    ))
    connection = ProjectGitHubRepository(
        project_id=project.id, installation_id=installation.id,
        github_repository_id=501, full_name="acme/api",
        html_url="https://github.com/acme/api", default_branch="main",
    )
    session.add(connection)
    session.commit()
    first = create_ticket(session, project, owner, title="First")
    second = create_ticket(session, project, owner, title="Second")
    return SimpleNamespace(
        project=project, connection=connection, first=first, second=second
    )

def test_extract_ticket_numbers_is_case_insensitive_and_boundary_aware():
    assert extract_ticket_numbers(
        "PAY", ["PAY-104 (pay-7) XPAY-2 PAY-0 PAY--1 PAY-9X"]
    ) == {7, 104}

def test_review_aggregation_uses_latest_effective_review_per_login():
    reviews = [
        {"user": {"login": "sam"}, "state": "APPROVED",
         "submitted_at": "2026-09-17T10:00:00Z"},
        {"user": {"login": "sam"}, "state": "CHANGES_REQUESTED",
         "submitted_at": "2026-09-17T11:00:00Z"},
    ]
    assert aggregate_review_state(reviews) is GitHubReviewState.CHANGES_REQUESTED

@pytest.mark.parametrize("status", ["waiting", "requested", "pending"])
def test_ci_waiting_requested_and_pending_are_pending(status):
    assert aggregate_ci_state([
        {"status": status, "conclusion": None}
    ]) is GitHubCIState.PENDING

def test_ci_stale_conclusion_is_failed_and_empty_is_none():
    assert aggregate_ci_state([
        {"status": "completed", "conclusion": "stale"}
    ]) is GitHubCIState.FAILED
    assert aggregate_ci_state([]) is GitHubCIState.NONE
```

- [ ] **Step 2: Write failing persistence reconciliation tests**

Add these concrete persistence tests:

```python
def _pull(title="PAY-1 PAY-2", body="", branch="feature/pay-1"):
    return {
        "id": 9001, "node_id": "PR_node_9001", "number": 17,
        "title": title, "body": body, "draft": False, "state": "open",
        "merged_at": None, "html_url": "https://github.com/acme/api/pull/17",
        "user": {"login": "octocat"},
        "head": {"ref": branch, "sha": "a" * 40},
        "updated_at": "2026-09-17T12:00:00Z",
    }

def test_pull_upsert_persists_mapping_and_reconciles_links(session, github_sync_world):
    world = github_sync_world
    artifact = upsert_pull_request(session, world.connection, _pull())
    reconcile_pull_request_links(
        session, world.connection, artifact, ["PAY-1 PAY-2", "", "feature/pay-1"]
    )
    session.commit()
    assert artifact.state is GitHubArtifactState.OPEN
    assert artifact.head_sha == "a" * 40
    assert artifact.occurred_at == datetime(2026, 9, 17, 12, tzinfo=UTC)
    linked = session.exec(
        select(Ticket.ticket_number).join(TicketGitLink, TicketGitLink.ticket_id == Ticket.id)
        .where(TicketGitLink.artifact_id == artifact.id).order_by(Ticket.ticket_number)
    ).all()
    assert linked == [1, 2]
    reconcile_pull_request_links(
        session, world.connection, artifact, ["PAY-2", None, None]
    )
    session.commit()
    linked = session.exec(
        select(Ticket.ticket_number).join(TicketGitLink, TicketGitLink.ticket_id == Ticket.id)
        .where(TicketGitLink.artifact_id == artifact.id)
    ).all()
    assert linked == [2]

def test_commit_uses_safe_html_url_and_keeps_first_links(session, github_sync_world):
    world = github_sync_world
    sha = "b" * 40
    commit = {
        "id": sha, "message": "PAY-1 fix timeout\nbody",
        "timestamp": "2026-09-17T13:00:00Z",
        "author": {"username": "sam", "name": "Sam"},
        "committer": {"username": "bot", "name": "Bot"},
        "url": "javascript:alert(1)",
    }
    artifact = insert_linked_commit(session, world.connection, commit)
    session.commit()
    assert artifact.html_url == f"https://github.com/acme/api/commit/{sha}"
    assert artifact.title == "PAY-1 fix timeout"
    assert artifact.author_login == "sam"
    replay = insert_linked_commit(
        session, world.connection, {**commit, "message": "PAY-2 changed"}
    )
    session.commit()
    assert replay.id == artifact.id
    links = session.exec(select(TicketGitLink).where(
        TicketGitLink.artifact_id == artifact.id
    )).all()
    assert [link.ticket_id for link in links] == [world.first.id]
    assert insert_linked_commit(
        session, world.connection, {**commit, "id": "c" * 40, "message": "no key"}
    ) is None
```

- [ ] **Step 3: Run the sync file and verify missing-function failure**

Run: `uv run pytest -q tests/test_github_sync.py`

Expected: collection fails on the new function imports.

- [ ] **Step 4: Implement the boundary-aware matcher and normalization**

Compile `rf"(?<![A-Z0-9]){re.escape(project_key)}-([1-9][0-9]*)(?![A-Z0-9])"` with `re.IGNORECASE`. Query tickets by both `project_id` and extracted `ticket_number`. Validate artifact URLs with `urlparse`: require `https`, no credentials, and hostname equal to `urlparse(settings.github_web_url).hostname`. Use this exact PR mapping and never mutate `Ticket.status`:

```python
state = (
    GitHubArtifactState.DRAFT if pull.get("draft")
    else GitHubArtifactState.MERGED if pull.get("merged_at")
    else GitHubArtifactState.CLOSED if pull.get("state") == "closed"
    else GitHubArtifactState.OPEN
)
artifact.head_sha = pull["head"]["sha"]
artifact.occurred_at = parse_github_datetime(pull["updated_at"])
```

- [ ] **Step 5: Implement aggregate and link rules**

For reviews, sort by `submitted_at`, collapse by `user.login`, and remove that reviewer's effective decision when state is `DISMISSED`. For checks, treat statuses `queued`, `in_progress`, `waiting`, `requested`, and `pending` as pending; treat conclusions `failure`, `cancelled`, `timed_out`, `action_required`, `startup_failure`, and `stale` as failed. Failed outranks pending, pending outranks passed, and an empty list is none. Pull-request reconciliation deletes stale `TicketGitLink` rows and inserts missing rows in the caller's transaction. Commit insertion returns `None` before creating an artifact if no valid project ticket matches, then constructs its URL without trusting `commit["url"]`:

```python
sha = commit.get("id", "")
if re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
    return None
html_url = f"{connection.html_url.rstrip('/')}/commit/{sha.lower()}"
title = commit.get("message", "").splitlines()[0][:500]
occurred_at = parse_github_datetime(commit["timestamp"])
```

- [ ] **Step 6: Run and commit the normalization slice**

Run:

```bash
uv run pytest -q tests/test_github_sync.py
uv run ruff check app/github_sync.py tests/test_github_sync.py
```

Expected: all tests pass and Ruff exits 0.

```bash
git add app/github_sync.py tests/test_github_sync.py
git commit -m "feat: normalize GitHub development activity"
```

---

### Task 5: Signed Idempotent GitHub Webhook Processing

**Files:**

- Create: `app/routers/github_webhook.py`
- Modify: `app/main.py`
- Modify: `app/github_sync.py`
- Create: `tests/test_github_webhook.py`

**Interfaces:**

- Produces route: `POST /integrations/github/webhook` with no cookie/session/CSRF dependency.
- Produces: `valid_webhook_signature(body: bytes, header: str | None, secret: str) -> bool`.
- Produces: `process_github_delivery(session: Session, delivery_id: str, event_type: str, payload: dict, github: GitHubClient) -> bool`; returns `False` for an already-recorded delivery and `True` after a committed new delivery.

- [ ] **Step 1: Write and run failing signature, parse-order, and deduplication tests**

Create `tests/test_github_webhook.py`. Sign fixture bytes with:

```python
signature = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
```

Assert missing, malformed, SHA-1, and wrong SHA-256 headers return 401. Monkeypatch `json.loads` and `app.routers.github_webhook.Session` to raise if called and prove neither JSON parsing nor a database session starts before a valid signature. Assert the endpoint works without login cookies and returns 503 when `GITHUB_WEBHOOK_SECRET` is absent. Assert malformed signed JSON returns 400. Post the same valid delivery twice and assert one `IntegrationDelivery(provider="GITHUB", event_type="ping")` row and two HTTP 200 responses.

Run: `uv run pytest -q tests/test_github_webhook.py -k 'signature or parse_order or duplicate_delivery'`

Expected: FAIL because the webhook route does not exist.

- [ ] **Step 2: Implement signature verification and delivery claiming, then make Step 1 green**

Do not inject `get_session`, because FastAPI resolves dependencies before route code. Verify bytes first, parse second, then open the session:

```python
@router.post("/integrations/github/webhook")
async def github_webhook(request: Request) -> Response:
    raw = await request.body()
    secret = settings.github_webhook_secret
    if not secret:
        raise HTTPException(503, "GitHub webhook is not configured")
    if not valid_webhook_signature(raw, request.headers.get("X-Hub-Signature-256"), secret):
        raise HTTPException(401, "Invalid GitHub webhook signature")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid GitHub webhook JSON") from None
    delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
    event_type = request.headers.get("X-GitHub-Event", "").strip()
    if not delivery_id or not event_type or not isinstance(payload, dict):
        raise HTTPException(400, "Invalid GitHub webhook metadata")
    with Session(engine) as session, GitHubClient() as github:
        process_github_delivery(session, delivery_id, event_type, payload, github)
    return Response(status_code=200)
```

For this first green step, query `(provider, delivery_id)` before inserting, return `False` for an existing row, and otherwise add/flush `IntegrationDelivery(provider="GITHUB", delivery_id=delivery_id, event_type=event_type)`. A simultaneous unique-conflict loser is deliberately still RED until Step 10. Run the Step 1 command again. Expected: PASS.

- [ ] **Step 3: Write and run failing pull-request event tests**

Parametrize actions `opened`, `edited`, `synchronize`, `reopened`, `ready_for_review`, `converted_to_draft`, and `closed`; each upserts from `payload["pull_request"]` and reconciles title, body, and `head.ref` ticket keys. Parametrize unrelated actions `assigned`, `unassigned`, `labeled`, and `unlabeled`; each records delivery without artifact mutation. Assert matching uses both repository and installation IDs and processes the same repository connected to two projects against each project key.

```python
@pytest.mark.parametrize("action", [
    "opened", "edited", "synchronize", "reopened",
    "ready_for_review", "converted_to_draft", "closed",
])
def test_pull_request_actions_refresh_artifact(action, signed_webhook, connected_repo):
    response = signed_webhook("pull_request", {**pull_payload, "action": action})
    assert response.status_code == 200
    assert stored_pull_request().head_sha == "a" * 40
```

Run: `uv run pytest -q tests/test_github_webhook.py -k 'pull_request_actions or repository_and_installation_lookup'`

Expected: FAIL because the pull-request branch is absent.

- [ ] **Step 4: Implement the pull-request branch and make Step 3 green**

Resolve active connections with this exact join/filter; malformed/missing identifiers record delivery without domain mutation:

```python
select(ProjectGitHubRepository).join(
    GitHubInstallation,
    GitHubInstallation.id == ProjectGitHubRepository.installation_id,
).where(
    ProjectGitHubRepository.github_repository_id == repository_id,
    GitHubInstallation.github_installation_id == installation_id,
    ProjectGitHubRepository.active.is_(True),
)
```

Only the seven tested actions call `upsert_pull_request` and `reconcile_pull_request_links`. Commit artifact/link changes and delivery together. Run the Step 3 command again. Expected: PASS.

- [ ] **Step 5: Write and run failing review/check aggregation event tests**

Parametrize `pull_request_review` actions `submitted`, `edited`, and `dismissed`; each calls `pull_request_reviews(installation_id, full_name, number)` and updates the existing PR artifact. Parametrize `check_run` actions `created`, `rerequested`, `completed`, and `requested_action`; each reads `check_run.head_sha`, calls `pull_requests_for_commit` and `check_runs`, then updates only existing PR artifacts whose number is returned for that same connection. Unknown actions record only the delivery.

Assert an `httpx.HTTPError` preserves the last aggregate, commits delivery, returns 200, and logs repository/PR identifiers without tokens or response bodies.

Run: `uv run pytest -q tests/test_github_webhook.py -k 'review_actions or check_run_actions or aggregate_refresh_failure'`

Expected: FAIL because review/check branches are absent.

- [ ] **Step 6: Implement review/check branches and make Step 5 green**

Use `payload["pull_request"]["number"]` for review lookup. For a check SHA, list associated PRs, then select artifacts by exact connection, `kind=PULL_REQUEST`, and returned number:

```python
artifact = session.exec(select(GitHubArtifact).where(
    GitHubArtifact.repository_connection_id == connection.id,
    GitHubArtifact.kind == GitHubArtifactKind.PULL_REQUEST,
    GitHubArtifact.number == pull_number,
)).first()
```

Catch only `httpx.HTTPError` around each external refresh, log IDs, and continue. Run the Step 5 command again. Expected: PASS.

- [ ] **Step 7: Write and run failing push/installation lifecycle tests**

Use small inline payload dicts and assert:

```text
push: inspect each commit message and store only linked commits;
installation_repositories removed: mark matching selected connections inactive;
installation_repositories added: record delivery only; settings fetches availability live and
OWNER selection is still required;
installation deleted/suspend: mark every installation connection inactive;
installation unsuspend: leave selection inactive until an OWNER saves it again;
installation created/new_permissions: record delivery only;
unknown event or action: record delivery and make no domain mutation.
```

For push, assert the safe SHA-derived HTML URL and field mapping from Task 4 and that payload `url` is ignored. For installation events, look up by `GitHubInstallation.github_installation_id`; for `installation_repositories`, additionally filter `repositories_removed[*].id`. External disablement sets `active=False, disconnected_at=None`. Assert any unexpected processing exception rolls back the delivery and returns 500 so GitHub can retry.

Run: `uv run pytest -q tests/test_github_webhook.py -k 'push or installation_repositories or installation_lifecycle or unknown_event'`

Expected: FAIL because push/installation branches are absent.

- [ ] **Step 8: Implement push/installation branches and make Step 7 green**

Push iterates `payload.get("commits", [])`, passes each commit to `insert_linked_commit`, and never reads its `url`. Installation repository removal and installation `deleted`/`suspend` use explicit lookup branches described in Step 7. `added`, `unsuspend`, `created`, `new_permissions`, and every unknown event/action record delivery only. Run the Step 7 command again. Expected: PASS.

- [ ] **Step 9: Write and run RED delivery-conflict and rollback tests**

Add these concrete tests to `tests/test_github_webhook.py`:

```python
def test_delivery_unique_conflict_is_treated_as_duplicate(session):
    session.add(IntegrationDelivery(
        provider="GITHUB", delivery_id="race-delivery", event_type="push"
    ))
    session.commit()
    loser = IntegrationDelivery(
        provider="GITHUB", delivery_id="race-delivery", event_type="push"
    )
    assert claim_github_delivery(session, loser) is False
    assert len(session.exec(select(IntegrationDelivery).where(
        IntegrationDelivery.delivery_id == "race-delivery"
    )).all()) == 1

def test_unexpected_processing_error_rolls_back_delivery(
    session, github_client, monkeypatch
):
    def explode(session, connection, commit):
        raise RuntimeError("boom")
    monkeypatch.setattr(github_sync, "insert_linked_commit", explode)
    with pytest.raises(RuntimeError, match="boom"):
        process_github_delivery(
            session, "retryable-delivery", "push",
            {"repository": {"id": 501}, "installation": {"id": 7001},
             "commits": [{"id": "a" * 40, "message": "PAY-1"}]},
            github_client,
        )
    assert session.exec(select(IntegrationDelivery).where(
        IntegrationDelivery.delivery_id == "retryable-delivery"
    )).first() is None
```

Run: `uv run pytest -q tests/test_github_webhook.py -k 'delivery_unique_conflict or unexpected_processing_error'`

Expected: RED because `claim_github_delivery` does not exist yet and the Step 2 claim path does not classify a simultaneous unique conflict or guarantee rollback around event dispatch.

- [ ] **Step 10: Implement atomic claim/rollback and make Step 9 GREEN**

Extract `claim_github_delivery(session: Session, delivery: IntegrationDelivery) -> bool`. Add/flush inside `session.begin_nested()`; return `False` only when the database error names `uq_integration_delivery_provider_delivery_id` or SQLite reports the exact columns `integration_delivery.provider, integration_delivery.delivery_id`, otherwise re-raise. Put the event action call in this transaction wrapper:

```python
try:
    if not claim_github_delivery(session, delivery):
        session.rollback()
        return False
    dispatch_github_event(session, event_type, payload, github)
    session.commit()
except Exception:
    session.rollback()
    raise
return True
```

Run the Step 9 command again. Expected: GREEN with one delivery row and a clean rollback after the injected runtime error.

- [ ] **Step 11: Run and commit the complete webhook slice**

Run:

```bash
uv run pytest -q tests/test_github_webhook.py tests/test_github_sync.py tests/test_origin_guard.py tests/test_csrf.py
uv run ruff check app/github_sync.py app/routers/github_webhook.py tests/test_github_webhook.py
```

Expected: all tests pass and Ruff exits 0.

```bash
git add app/github_sync.py app/routers/github_webhook.py app/main.py tests/test_github_webhook.py
git commit -m "feat: process GitHub webhooks"
```

---

### Task 6: Initial Open Pull-Request Synchronization and Retry

**Files:**

- Modify: `app/github_sync.py`
- Modify: `app/routers/web_sprints.py`
- Modify: `app/templates/project_settings.html`
- Modify: `tests/test_web_project_settings.py`
- Create: `tests/test_github_initial_sync.py`

**Interfaces:**

- Produces: `sync_open_pull_requests(session: Session, connection: ProjectGitHubRepository, github: GitHubClient) -> int` returning the count of imported linked pull requests.
- Produces: `POST /projects/{slug}/settings/integrations/github/sync`.
- Consumes Task 4 normalization and Task 2 GitHub HTTP methods.

- [ ] **Step 1: Write failing sync service tests**

Create `tests/test_github_initial_sync.py` with this runnable fake and service test (import the Task 4 fixture/helper at module scope so pytest registers the fixture):

```python
from tests.test_github_sync import _pull, github_sync_world

class FakeGitHub:
    def __init__(self):
        self.review_calls = []
        self.check_calls = []

    def open_pull_requests(self, installation_id, full_name):
        assert (installation_id, full_name) == (7001, "acme/api")
        return [
            _pull(title="PAY-1 first"),
            {**_pull(title="second", body="PAY-2"), "id": 9002,
             "node_id": "PR_node_9002", "number": 18, "head": {
                 "ref": "feature/second", "sha": "b" * 40}},
            {**_pull(title="unlinked", body=""), "id": 9003,
             "node_id": "PR_node_9003", "number": 19, "head": {
                 "ref": "feature/unlinked", "sha": "c" * 40}},
        ]

    def pull_request_reviews(self, installation_id, full_name, number):
        self.review_calls.append((installation_id, full_name, number))
        return []

    def check_runs(self, installation_id, full_name, sha):
        self.check_calls.append((installation_id, full_name, sha))
        return []

def test_initial_sync_imports_only_linked_open_pull_requests(
    session, github_sync_world
):
    github = FakeGitHub()
    count = sync_open_pull_requests(
        session, github_sync_world.connection, github
    )
    session.commit()
    assert count == 2
    assert [call[2] for call in github.review_calls] == [17, 18]
    assert [call[2] for call in github.check_calls] == ["a" * 40, "b" * 40]
    assert sync_open_pull_requests(
        session, github_sync_world.connection, github
    ) == 2
    assert len(session.exec(select(GitHubArtifact)).all()) == 2
```

- [ ] **Step 2: Write failing route retry tests**

Add these concrete route/transaction tests to `tests/test_web_project_settings.py`:

```python
def test_selection_commits_before_independent_repository_sync(
    client, engine, settings_world, login_as, github_selection, monkeypatch
):
    seen = []
    def fake_sync(session, connection, github):
        with Session(engine) as observer:
            assert observer.get(ProjectGitHubRepository, connection.id).active is True
        seen.append(connection.github_repository_id)
        if connection.github_repository_id == 502:
            raise httpx.ConnectError("repository unavailable")
        session.add(GitHubArtifact(
            repository_connection_id=connection.id,
            kind=GitHubArtifactKind.PULL_REQUEST, external_id="pr-501", number=17,
            title="PAY-1", html_url="https://github.com/acme/api/pull/17",
            author_login="sam", state=GitHubArtifactState.OPEN,
            ci_state=GitHubCIState.NONE, occurred_at=utcnow(),
        ))
    monkeypatch.setattr(web_sprints, "sync_open_pull_requests", fake_sync)
    login_as(settings_world.owner.email)
    response = client.post(
        f"/projects/{settings_world.project.slug}/settings/integrations/github/repositories",
        data={"state": github_selection.state,
              "repository_ids": ["501", "502"], **_csrf(settings_world.owner)},
    )
    assert response.status_code == 200
    assert seen == [501, 502]
    assert "Retry sync" in response.text and "acme/web" in response.text
    with Session(engine) as observer:
        assert len(observer.exec(select(ProjectGitHubRepository)).all()) == 2
        assert len(observer.exec(select(GitHubArtifact)).all()) == 1

def test_retry_sync_requires_owner_and_csrf(
    client, settings_world, login_as, connected_github
):
    url = f"/projects/{settings_world.project.slug}/settings/integrations/github/sync"
    login_as(settings_world.member.email)
    assert client.post(url, data=_csrf(settings_world.member)).status_code == 403
    login_as(settings_world.owner.email)
    assert client.post(url, data={}).status_code == 403
    response = client.post(
        url, data=_csrf(settings_world.owner), follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("?github_synced=1")
```

- [ ] **Step 3: Run the sync tests and verify missing-function failure**

Run: `uv run pytest -q tests/test_github_initial_sync.py`

Expected: collection fails on `sync_open_pull_requests`.

- [ ] **Step 4: Implement bounded per-repository sync**

After Task 3's repository-selection POST has committed all selected rows, iterate their IDs. For each ID, begin an independent transaction, reload the connection and installation, call `open_pull_requests(installation.github_installation_id, connection.full_name)`, reject non-open payloads, import linked PRs, and fetch reviews/checks only for linked PRs. Commit that repository on success; on `httpx.HTTPError`, roll back that repository before continuing to the next:

```python
connections = save_project_repositories(session, project, installation, selected)
connection_ids = [connection.id for connection in connections]
for connection_id in connection_ids:
    try:
        session.begin()
        connection = session.get(ProjectGitHubRepository, connection_id)
        sync_open_pull_requests(session, connection, github)
        session.commit()
    except httpx.HTTPError:
        session.rollback()
        failed_repository_ids.append(connection_id)
```

`save_project_repositories` commits before returning, so any sync rollback cannot undo selection or another repository's successful sync. Add Retry sync to `project_settings.html` only now; its OWNER-only, CSRF-protected route retries all active project repositories with the same independent commit/rollback loop. Do not add a queue or polling service.

- [ ] **Step 5: Run and commit the initial-sync slice**

Run:

```bash
uv run pytest -q tests/test_github_initial_sync.py tests/test_web_project_settings.py tests/test_github_sync.py
uv run ruff check app/github_sync.py app/routers/web_sprints.py tests/test_github_initial_sync.py tests/test_web_project_settings.py
```

Expected: all tests pass and Ruff exits 0.

```bash
git add app/github_sync.py app/routers/web_sprints.py app/templates/project_settings.html tests/test_github_initial_sync.py tests/test_web_project_settings.py
git commit -m "feat: sync open GitHub pull requests"
```

---

### Task 7: Ticket Key, Branch Command, and Development UI

**Files:**

- Modify: `app/github_sync.py`
- Modify: `app/routers/web.py`
- Modify: `app/templates/partials/ticket_detail.html`
- Modify: `app/static/app.js`
- Modify: `app/static/app.css`
- Modify: `.interface-design/system.md`
- Create: `tests/test_web_ticket_development.py`

**Interfaces:**

- Produces frozen dataclass `TicketDevelopmentRow(repository_full_name: str, kind: GitHubArtifactKind, number: int | None, title: str, html_url: str | None, author_login: str, state: GitHubArtifactState | None, review_state: GitHubReviewState | None, ci_state: GitHubCIState, occurred_at: datetime)`.
- Produces: `ticket_development(session: Session, ticket: Ticket) -> list[TicketDevelopmentRow]`; returns only artifacts reached through active repository connections.
- Produces: `ticket_reference(project: Project, ticket: Ticket) -> str` as `KEY-number`.
- Produces: `branch_command(project: Project, ticket: Ticket) -> str` as `git checkout -b feature/key-number-title-slug` using existing `app.services.slugify`.

- [ ] **Step 1: Write failing rendering and security tests**

Create `tests/test_web_ticket_development.py`. Assert OWNER and MEMBER ticket detail responses contain `PAY-104`, `git checkout -b feature/pay-104-fix-timeout`, linked repository/PR/commit, review state, CI state, author, and `<time datetime="2026-09-17T12:00:00+00:00" data-local-time>`. Assert inactive repository links are hidden without deleting history.

Store a title containing `<script>`, an `html_url` using `javascript:`, and a repository name containing HTML. Assert text is escaped, the invalid URL is not rendered as a link, and valid GitHub links have `target="_blank" rel="noopener noreferrer"`. Assert the existing comments panel remains after the development section and both drawer/full-page detail modes render the same data. Use `2026-09-17T12:00:00+00:00` as the exact `<time datetime>` test value.

- [ ] **Step 2: Write failing clipboard and responsive assertions**

Assert both copy buttons are real `<button type="button">` elements with `data-copy-text`, an adjacent `role="status" aria-live="polite"`, and visible success text after the delegated click handler. Read `app/static/app.js` and assert it contains exactly one `document.addEventListener("click"`; delegation requires no `DOMContentLoaded` or `htmx:afterSwap` copy initialization. Extend the existing UI CSS test to assert the ticket detail remains 60/40 at desktop width and becomes one column at the existing mobile breakpoint.

- [ ] **Step 3: Run the UI test and verify missing-content failure**

Run: `uv run pytest -q tests/test_web_ticket_development.py tests/test_web_ticket_detail.py tests/test_web_ui_refresh.py`

Expected: the new `PAY-104` and Development assertions fail.

- [ ] **Step 4: Add development context to the shared detail renderer**

Define `TicketDevelopmentRow` with the exact fields and types in Interfaces. In `_ticket_detail()`, calculate the ticket reference, branch command, and `ticket_development()` rows once and pass them to `partials/ticket_detail.html`; this automatically covers drawer, full page, validation re-render, comment error re-render, and saved state. Order rows by `occurred_at` descending. Do not expose inactive connections.

- [ ] **Step 5: Add accessible markup and delegated copy behavior**

Keep the header inside the left pane, add compact PR/CI badges there, and place Development below ticket fields before the comments include. Extend the existing single delegated document click handler in `app.js`; do not register another click listener:

```javascript
const copyButton = event.target.closest("[data-copy-text]");
if (copyButton) {
  const status = copyButton.nextElementSibling;
  navigator.clipboard.writeText(copyButton.dataset.copyText).then(
    () => { status.textContent = "Copied"; },
    () => { status.textContent = "Copy failed"; },
  );
  return;
}
```

Because the listener is delegated on `document`, it works for initial and HTMX-swapped markup without initialization hooks.

- [ ] **Step 6: Run and commit the UI slice**

Run:

```bash
uv run pytest -q tests/test_web_ticket_development.py tests/test_web_ticket_detail.py tests/test_web_ui_refresh.py tests/test_web_ticket_comments.py
uv run ruff check app/github_sync.py app/routers/web.py tests/test_web_ticket_development.py
```

Expected: all tests pass and Ruff exits 0.

```bash
git add app/github_sync.py app/routers/web.py app/templates/partials/ticket_detail.html app/static/app.js app/static/app.css .interface-design/system.md tests/test_web_ticket_development.py
git commit -m "feat: show GitHub activity on tickets"
```

---

### Task 8: End-to-End Verification and Graph Refresh

**Files:**

- Modify only files already named in Tasks 1–7 if verification exposes a defect.

- [ ] **Step 1: Run focused security and authorization regression tests**

Run:

```bash
uv run pytest -q tests/test_github_state.py tests/test_github_client.py tests/test_web_project_settings.py tests/test_github_webhook.py tests/test_github_initial_sync.py tests/test_web_ticket_development.py tests/test_authorization_matrix.py tests/test_origin_guard.py tests/test_csrf.py
```

Expected: all tests pass; no request can mutate GitHub configuration as a MEMBER; invalid webhook signatures create no delivery row.

- [ ] **Step 2: Run migration and full-suite verification**

Run:

```bash
uv run ruff check .
git diff --check
github_verify_dir=$(mktemp -d /tmp/kanbanflow-github-verify.XXXXXX)
github_verify_db="$github_verify_dir/verify.db"
DATABASE_URL="sqlite:///$github_verify_db" uv run alembic upgrade head
DATABASE_URL="sqlite:///$github_verify_db" uv run alembic downgrade -1
DATABASE_URL="sqlite:///$github_verify_db" uv run alembic upgrade head
uv run pytest -q
```

Expected: every command exits 0 with no test failures.

- [ ] **Step 3: Run the deterministic browser-facing acceptance tests**

Run:

```bash
uv run pytest -q tests/test_web_project_settings.py tests/test_github_initial_sync.py tests/test_web_ticket_development.py tests/test_web_ticket_detail.py tests/test_web_ui_refresh.py
```

Expected: the MockTransport-backed OWNER flow connects and selects two repositories, partial sync failure is retryable, MEMBER output has no controls, and drawer/full-page ticket output passes link, copy-status, time, focus, comments, and mobile-layout assertions.

- [ ] **Step 4: Refresh the knowledge graph and inspect the final diff**

Run:

```bash
graphify update .
git status --short
git diff --stat
```

Expected: graph update exits 0 and the diff contains only this plan's GitHub integration files plus expected `graphify-out/` updates.

- [ ] **Step 5: Commit verification fixes only if Step 1–4 changed implementation files**

```bash
git add pyproject.toml uv.lock app/config.py app/models.py app/services.py app/github.py app/github_sync.py app/main.py app/routers/web_sprints.py app/routers/github_webhook.py app/routers/web.py app/templates/project_settings.html app/templates/partials/ticket_detail.html app/static/app.js app/static/app.css .interface-design/system.md alembic/versions/42f6c8e1ad30_add_github_integration.py tests/test_github_models.py tests/test_migrations.py tests/test_github_client.py tests/test_github_state.py tests/test_web_project_settings.py tests/test_github_sync.py tests/test_github_webhook.py tests/test_github_initial_sync.py tests/test_web_ticket_development.py
git commit -m "fix: complete GitHub sync verification"
```

Skip this commit when verification required no fix.
