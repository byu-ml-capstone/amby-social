# Integration Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add immutable project keys and independent Slack, Teams, and Discord webhooks in Project Settings.

**Architecture:** This foundation delivers keys, migrated chat storage, settings cards, and fan-out as one runnable cutover. GitHub OAuth, sync, artifacts, and modal UI are deferred; GitHub is a non-actionable Coming soon card.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Alembic, Jinja2, HTMX, pytest

**Spec:** docs/superpowers/specs/2026-09-17-project-integrations-design.md

## Global Constraints

- Keep the centered ticket modal and comments; add no frontend framework or queue.
- OWNERs mutate; MEMBERs see non-secret status. Stored URLs never appear in HTML or Project API output.
- Keys are immutable, globally unique, uppercase alphanumeric, and at most 10 characters.
- Reject loopback, private, link-local, multicast, reserved, and unspecified literal IP hosts. Do not DNS-resolve hostnames.
- One destination failure cannot block another.

---

### Task 1: Immutable Project Keys

**Files:** Modify app/models.py, app/services.py, app/schemas.py, app/routers/api_projects.py, app/templates/project_settings.html, tests/conftest.py, tests/test_models.py, tests/test_projects.py, tests/test_concurrency.py, tests/test_benchmark.py, tests/test_migrations.py. Create alembic/versions/d93f7a21c4e8_add_project_keys.py and tests/test_project_keys.py.

**Interfaces:** Project.key: str is non-null, indexed, unique, max_length=10. allocate_project_key(session: Session, name: str) -> str. create_project(session: Session, name: str, user: User) -> Project. ProjectOut.key: str.

- [ ] **Step 1: Write failing tests and repair valid fixtures**

    def test_project_key_is_stable_and_collision_safe(session, make_user):
        owner = make_user(email="key-owner@example.com")
        first = create_project(session, "Payment Gateway", owner)
        second = create_project(session, "Payments Admin", owner)
        update_project(session, first, name="Checkout")
        assert (first.key, second.key) == ("PAY", "PAY2")

Change make_project(owner, name) to call create_project(session, name, owner). Give direct Project(...) setup in the listed tests explicit unique keys. Add assertions for uppercase/alphanumeric format, length 10, ProjectOut.key, read-only settings display, and a second session retrying a project.key unique collision.

- [ ] **Step 2: Verify failure**

Run: uv run pytest -q tests/test_project_keys.py

Expected: FAIL because Project.key does not exist.

- [ ] **Step 3: Implement allocation and retry**

    def allocate_project_key(session: Session, name: str) -> str:
        token = next(iter(name.split()), "")
        base = "".join(c for c in token.upper() if c.isascii() and c.isalnum())[:3] or "PRJ"
        suffix = 1
        while True:
            key = base if suffix == 1 else f"{base[:10 - len(str(suffix))]}{suffix}"
            if session.exec(select(Project.id).where(Project.key == key)).first() is None:
                return key
            suffix += 1

Allocate during create_project. If flush raises SQLite UNIQUE constraint failed: project.key, roll back and retry allocation; retain slug-conflict handling for slug collisions. Expose key through ProjectOut/_out(), never ProjectUpdate, and display only {{ project.key }}.

- [ ] **Step 4: Add and test migration**

Create d93f7a21c4e8_add_project_keys.py with revision d93f7a21c4e8 and down_revision c41d8e7f2a10. Upgrade adds nullable key, backfills by created_at/id with the same rule, then explicitly runs:

    op.create_index("ix_project_key", "project", ["key"], unique=True)

After that, make key non-null. Downgrade runs op.drop_index("ix_project_key", table_name="project") then removes key.

    def test_key_migration_backfills_existing_projects(tmp_path):
        db = tmp_path / "legacy.db"
        env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
        subprocess.run(["uv", "run", "alembic", "upgrade", "c41d8e7f2a10"], check=True, env=env)
        with make_engine(f"sqlite:///{db}").begin() as c:
            c.execute(text("INSERT INTO project (id,name,slug,webhook_type,next_ticket_number,created_at) VALUES ('a','Payment Gateway','payment-gateway','NONE',1,'2026-01-01')"))
            c.execute(text("INSERT INTO project (id,name,slug,webhook_type,next_ticket_number,created_at) VALUES ('b','Payments Admin','payments-admin','NONE',1,'2026-01-02')"))
        subprocess.run(["uv", "run", "alembic", "upgrade", "d93f7a21c4e8"], check=True, env=env)
        with make_engine(f"sqlite:///{db}").connect() as c:
            assert c.execute(text("SELECT key FROM project ORDER BY id")).scalars().all() == ["PAY", "PAY2"]

Then assert downgrade removes the column:

    subprocess.run(["uv", "run", "alembic", "downgrade", "c41d8e7f2a10"], check=True, env=env)
    columns = {column["name"] for column in inspect(make_engine(f"sqlite:///{db}")).get_columns("project")}
    assert "key" not in columns

Run: uv run pytest -q tests/test_project_keys.py tests/test_migrations.py tests/test_models.py tests/test_projects.py tests/test_concurrency.py. Expected: PASS.

- [ ] **Step 5: Commit**

    git add app/models.py app/services.py app/schemas.py app/routers/api_projects.py app/templates/project_settings.html tests/conftest.py tests/test_models.py tests/test_projects.py tests/test_concurrency.py tests/test_benchmark.py tests/test_migrations.py tests/test_project_keys.py alembic/versions/d93f7a21c4e8_add_project_keys.py
    git commit -m "feat: add immutable project keys"

---

### Task 2: Chat Cutover, Settings, and Fan-Out

**Files:** Modify app/models.py, app/services.py, app/schemas.py, app/notifications.py, app/routers/api_projects.py, app/routers/web.py, app/routers/web_sprints.py, app/templates/project_settings.html, app/static/app.css, tests/test_migrations.py, tests/test_project_access.py, tests/test_web_project_settings.py, tests/test_web_final_fixes.py, tests/test_notification_wiring.py, tests/test_web_ticket_comments.py, tests/test_web_ticket_detail.py. Create alembic/versions/e4b9c52d8fa1_add_chat_webhooks.py and tests/test_chat_integrations.py.

**Interfaces:** ProjectChatWebhook(id: str, project_id: str, provider: WebhookType, url: str, created_at: datetime, updated_at: datetime). set_chat_webhook(session, project, provider, url) -> ProjectChatWebhook. disconnect_chat_webhook(session, project, provider) -> None. chat_webhooks(session, project_id) -> list[ProjectChatWebhook]. schedule(tasks, session, project, event, ticket) -> None. schedule_comment_mention(tasks, session, project, ticket, author, mentioned_users, body) -> None.

- [ ] **Step 1: Write all failing cutover tests**

    def test_project_can_store_each_chat_provider(session, make_user, make_project):
        project = make_project(make_user(email="chat-owner@example.com"))
        set_chat_webhook(session, project, WebhookType.SLACK, "https://hooks.example.test/slack")
        set_chat_webhook(session, project, WebhookType.DISCORD, "https://hooks.example.test/discord")
        assert [row.provider for row in chat_webhooks(session, project.id)] == [WebhookType.DISCORD, WebhookType.SLACK]

    @pytest.mark.parametrize(("url", "email"), [
        ("https://127.0.0.1/hook", "loopback@example.test"),
        ("https://10.0.0.1/hook", "private@example.test"),
        ("https://169.254.1.1/hook", "linklocal@example.test"),
        ("https://224.0.0.1/hook", "multicast@example.test"),
        ("https://240.0.0.1/hook", "reserved@example.test"),
        ("https://0.0.0.0/hook", "unspecified@example.test"),
    ])
    def test_chat_webhook_rejects_disallowed_literal_ip(session, make_user, make_project, url, email):
        owner = make_user(email=email)
        project = make_project(owner)
        with pytest.raises(HTTPException, match="safe https URL"):
            set_chat_webhook(session, project, WebhookType.SLACK, url)

    def test_deleting_project_deletes_chat_webhooks(session, make_user, make_project):
        project = make_project(make_user(email="delete-owner@example.com"))
        set_chat_webhook(session, project, WebhookType.SLACK, "https://hooks.example.test/x")
        delete_project(session, project, project.slug)
        assert chat_webhooks(session, project.id) == []

Use settings_world and _csrf(settings_world.owner) in web tests. Add these exact assertions:

    assert client.post(member_url, data={"url": "https://hooks.example.test/x", **_csrf(settings_world.member)}).status_code == 403
    assert 'name="url"' not in member_page.text
    assert secret not in owner_page.text
    assert client.post(disconnect_url, data={"confirm": "Disconnect", **_csrf(settings_world.owner)}).status_code == 303
    assert chat_webhooks(session, settings_world.project.id) == []

Add test_replacing_provider_keeps_one_row, test_disconnect_is_idempotent, test_none_provider_is_rejected, test_url_over_500_is_rejected, test_http_url_is_rejected, and test_hostname_without_dns_lookup. The latter monkeypatches socket.getaddrinfo to raise AssertionError and persists https://hooks.example.test/x successfully.

- [ ] **Step 2: Verify failure**

Run: uv run pytest -q tests/test_chat_integrations.py tests/test_notification_wiring.py tests/test_web_project_settings.py

Expected: FAIL because storage and fan-out do not exist.

- [ ] **Step 3: Implement storage, safety, cutover, and fan-out together**

ProjectChatWebhook has UUID id, indexed project.id FK, URL max length 500, timestamps, unique constraint uq_project_chat_webhook_provider on project_id/provider, and:

    CheckConstraint("provider IN ('SLACK', 'TEAMS', 'DISCORD')", name="ck_chat_webhook_provider")

Extract validate_webhook_url from update_project. Preserve existing syntax checks; use ipaddress.ip_address only for literal hosts and reject is_loopback, is_private, is_link_local, is_multicast, is_reserved, or is_unspecified. A ValueError means hostname: accept without DNS lookup. set_chat_webhook rejects NONE and inserts or updates url/updated_at; disconnect commits even when absent; delete_project deletes child rows before Project.

Remove legacy fields from Project, ProjectUpdate, ProjectOut, _out(), the old settings form, and its route parameters. Add connect/disconnect to existing web_sprints.router with project_owner plus verify_csrf; accept only uppercase SLACK/TEAMS/DISCORD and return 404 otherwise; require confirm == "Disconnect".

    def schedule(tasks, session, project, event, ticket):
        if tasks is None:
            return
        payload = build_payload(project, event, ticket)
        for webhook in chat_webhooks(session, project.id):
            tasks.add_task(dispatch, webhook.provider, webhook.url, payload)

Pass session from create_ticket/set_status in services.py and comment create/edit in web.py. Update every direct schedule call in tests/test_notification_wiring.py to schedule(tasks, session, project, event, ticket), and every direct schedule_comment_mention call in tests/test_web_ticket_comments.py to take session second. Replace every direct legacy webhook assignment with set_chat_webhook. Do not change api_tickets.py: it has no schedule call.

Extend tests/test_notification_wiring.py imports with `import httpx`, `import app.notifications as notifications`, `ProjectChatWebhook`, and `set_chat_webhook`, then add:

    def test_failed_first_dispatch_does_not_prevent_second(
        session, make_user, make_project, monkeypatch
    ):
        owner = make_user(email="fanout-owner@example.com")
        project = session.get(Project, make_project(owner).id)
        first_url = "https://hooks.example.test/first"
        second_url = "https://hooks.example.test/second"
        set_chat_webhook(session, project, WebhookType.SLACK, first_url)
        set_chat_webhook(session, project, WebhookType.TEAMS, second_url)
        rows = session.exec(
            select(ProjectChatWebhook)
            .where(ProjectChatWebhook.project_id == project.id)
            .order_by(ProjectChatWebhook.provider)
        ).all()
        assert [(row.provider, row.url) for row in rows] == [
            (WebhookType.SLACK, first_url),
            (WebhookType.TEAMS, second_url),
        ]

        ticket = create_ticket(session, project, owner, title="Fan out")
        tasks = _RecordingTasks()
        schedule(tasks, session, project, EVENT_TICKET_CREATED, ticket)
        assert len(tasks.calls) == 2
        assert [args[1] for _, args, _ in tasks.calls] == [first_url, second_url]

        attempted = []

        def post(_client, url, **kwargs):
            attempted.append(url)
            if url == first_url:
                raise httpx.ConnectError("down")
            return httpx.Response(200)

        monkeypatch.setattr(httpx.Client, "post", post)
        monkeypatch.setattr(notifications, "BACKOFF_SECONDS", ())
        for func, args, kwargs in tasks.calls:
            func(*args, **kwargs)

        assert attempted == [first_url, second_url]

- [ ] **Step 4: Add cards and migration tests**

Render cards between Project Details and Members. OWNER has blank Connect/Replace inputs and separate disconnect forms; MEMBER has only name/status. Render GitHub Coming soon with no form. Controls have 40px minimum height and no saved URL in source.

Create e4b9c52d8fa1_add_chat_webhooks.py with revision e4b9c52d8fa1/down_revision d93f7a21c4e8. Use these file imports:

    import uuid
    from datetime import UTC, datetime

    import sqlalchemy as sa
    import sqlmodel
    from alembic import op

Upgrade creates the table, then runs this complete copy block before dropping the legacy columns:

    connection = op.get_bind()
    configured_rows = connection.execute(
        sa.text(
            """
            SELECT id AS project_id, webhook_type AS provider, webhook_url AS url
            FROM project
            WHERE webhook_type != 'NONE' AND webhook_url IS NOT NULL
            ORDER BY id
            """
        )
    ).mappings().all()
    copied_at = datetime.now(UTC).replace(tzinfo=None)
    for row in configured_rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO project_chat_webhook
                    (id, project_id, provider, url, created_at, updated_at)
                VALUES
                    (:id, :project_id, :provider, :url, :created_at, :updated_at)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "project_id": row["project_id"],
                "provider": row["provider"],
                "url": row["url"],
                "created_at": copied_at,
                "updated_at": copied_at,
            },
        )

Then batch-drop legacy columns. Downgrade restores columns, selects Slack then Teams then Discord per project, then drops the table.

    def test_chat_migration_round_trip(tmp_path):
        db = tmp_path / "legacy-chat.db"
        env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
        subprocess.run(["uv", "run", "alembic", "upgrade", "d93f7a21c4e8"], check=True, env=env)
        with make_engine(f"sqlite:///{db}").begin() as c:
            c.execute(text("INSERT INTO project (id,name,slug,key,webhook_type,webhook_url,next_ticket_number,created_at) VALUES ('p','Project','project','PRJ','SLACK','https://hooks.example.test/legacy',1,'2026-01-01')"))
        subprocess.run(["uv", "run", "alembic", "upgrade", "e4b9c52d8fa1"], check=True, env=env)
        with make_engine(f"sqlite:///{db}").connect() as c:
            assert c.execute(text("SELECT provider,url FROM project_chat_webhook")).one() == ("SLACK", "https://hooks.example.test/legacy")
        subprocess.run(["uv", "run", "alembic", "downgrade", "d93f7a21c4e8"], check=True, env=env)
        with make_engine(f"sqlite:///{db}").connect() as c:
            assert c.execute(text("SELECT webhook_type,webhook_url FROM project WHERE id='p'")).one() == ("SLACK", "https://hooks.example.test/legacy")

Run: uv run pytest -q tests/test_chat_integrations.py tests/test_notification_wiring.py tests/test_dispatch.py tests/test_formatters.py tests/test_project_access.py tests/test_web_project_settings.py tests/test_web_ticket_comments.py tests/test_web_ticket_detail.py tests/test_web_final_fixes.py tests/test_migrations.py. Expected: PASS.

- [ ] **Step 5: Commit**

    git add app/models.py app/services.py app/schemas.py app/notifications.py app/routers/api_projects.py app/routers/web.py app/routers/web_sprints.py app/templates/project_settings.html app/static/app.css tests/test_migrations.py tests/test_project_access.py tests/test_web_project_settings.py tests/test_web_final_fixes.py tests/test_notification_wiring.py tests/test_web_ticket_comments.py tests/test_web_ticket_detail.py tests/test_chat_integrations.py alembic/versions/e4b9c52d8fa1_add_chat_webhooks.py
    git commit -m "feat: add project chat integrations"

---

### Task 3: Foundation Verification

- [ ] **Step 1: Run verification**

    uv run ruff check .
    git diff --check
    DATABASE_URL=sqlite:////tmp/kanbanflow-integration-migration.db uv run alembic upgrade head
    DATABASE_URL=sqlite:////tmp/kanbanflow-integration-migration.db uv run alembic downgrade d93f7a21c4e8
    DATABASE_URL=sqlite:////tmp/kanbanflow-integration-migration.db uv run alembic upgrade head
    uv run pytest -q
    graphify update .

Expected: every command exits 0.

- [ ] **Step 2: Browser check and conditional commit**

As OWNER and MEMBER, check key immutability, three simultaneous chat connections, status-only secret-free MEMBER cards, disconnect confirmation, and the non-actionable GitHub card. Run git status --short. If verification changed named source/test files, stage those exact paths and commit with message fix: complete integration foundation verification. If nothing changed, do not commit.
