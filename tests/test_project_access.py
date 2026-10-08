import pytest
from fastapi import HTTPException

from app.auth import project_owner, project_reader, project_writer
from app.models import Role


def test_member_can_read_but_not_edit_settings(
    client, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, make_user(email="cat@example.com"))
    login_as("cat@example.com")
    assert client.get(f"/api/v1/projects/{project.slug}").status_code == 200
    assert client.patch(f"/api/v1/projects/{project.slug}", json={"name": "New"}).status_code == 403


def test_owner_can_edit_name_but_slug_never_changes(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner, name="Payment Gateway")
    login_as("ada@example.com")
    response = client.patch(f"/api/v1/projects/{project.slug}", json={"name": "Billing"})
    assert response.status_code == 200
    assert response.json()["name"] == "Billing"
    assert response.json()["slug"] == "payment-gateway"


def test_non_member_reads_404_and_writes_403(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    assert client.get(f"/api/v1/projects/{project.slug}").status_code == 404
    assert client.patch(f"/api/v1/projects/{project.slug}", json={"name": "X"}).status_code == 403


def test_missing_project_is_404_even_for_writes(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    assert client.patch("/api/v1/projects/nope", json={"name": "X"}).status_code == 404


def test_project_api_does_not_expose_legacy_webhook_fields(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    response = client.patch(
        f"/api/v1/projects/{project.slug}",
        json={"webhook_type": "SLACK", "webhook_url": "https://example.com/hook"},
    )

    assert response.status_code == 200
    assert "webhook_type" not in response.json()
    assert "webhook_url" not in response.json()


def test_delete_requires_confirmation(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert client.delete(f"/api/v1/projects/{project.slug}").status_code == 422
    assert (
        client.delete(f"/api/v1/projects/{project.slug}?confirm={project.slug}").status_code == 204
    )


# --- Additions beyond the brief's verbatim tests ---
#
# The brief's tests already pair the non-member 404 (test_non_member_reads_404_and_writes_403)
# with a member 200 on the same GET URL (test_member_can_read_but_not_edit_settings), which is
# what RULING R18 asks for. The tests below round out the authorization matrix: they cover
# project_reader's missing-project case (no route test above hits GET on a nonexistent slug),
# project_writer directly (it's never wired to a route on its own — only project_owner calls
# it internally — so its "member, non-owner succeeds" outcome has no HTTP path to exercise it),
# D-04 slug immutability with the old URL re-resolving after a rename, and DELETE's exact-match
# (not just "truthy") confirmation check.


def test_missing_project_is_404_on_read(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    assert client.get("/api/v1/projects/nope").status_code == 404


def test_rename_project_keeps_slug_and_old_url_resolves(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner, name="Payment Gateway")
    login_as("ada@example.com")

    response = client.patch(f"/api/v1/projects/{project.slug}", json={"name": "Billing"})
    assert response.status_code == 200
    assert response.json()["slug"] == project.slug

    # The old URL (built from the never-changed slug) still resolves to the renamed project.
    reread = client.get(f"/api/v1/projects/{project.slug}")
    assert reread.status_code == 200
    assert reread.json()["name"] == "Billing"
    assert reread.json()["slug"] == project.slug


def test_project_writer_dependency_covers_missing_non_member_and_member(
    session, make_user, make_project, add_member
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    member_user = make_user(email="cat@example.com")
    add_member(project, member_user)
    outsider = make_user(email="bob@example.com")

    with pytest.raises(HTTPException) as exc_info:
        project_writer("nope", owner, session)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        project_writer(project.slug, outsider, session)
    assert exc_info.value.status_code == 403

    result_project, result_member = project_writer(project.slug, member_user, session)
    assert result_project.id == project.id
    assert result_member.role == Role.MEMBER

    result_project, result_member = project_writer(project.slug, owner, session)
    assert result_member.role == Role.OWNER


def test_project_reader_dependency_missing_project_raises_404(session, make_user):
    user = make_user(email="ada@example.com")
    with pytest.raises(HTTPException) as exc_info:
        project_reader("nope", user, session)
    assert exc_info.value.status_code == 404


def test_project_owner_dependency_member_non_owner_raises_403(
    session, make_user, make_project, add_member
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    member_user = make_user(email="cat@example.com")
    add_member(project, member_user)

    with pytest.raises(HTTPException) as exc_info:
        project_owner(project.slug, member_user, session)
    assert exc_info.value.status_code == 403


def test_delete_rejects_a_mismatched_confirmation_value(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.delete(f"/api/v1/projects/{project.slug}?confirm=not-the-slug")
    assert response.status_code == 422
    # The project must still exist since the mismatched confirm was rejected.
    assert client.get(f"/api/v1/projects/{project.slug}").status_code == 200


def test_member_cannot_delete_project(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    add_member(project, make_user(email="cat@example.com"))
    login_as("cat@example.com")
    response = client.delete(f"/api/v1/projects/{project.slug}?confirm={project.slug}")
    assert response.status_code == 403


def test_whitespace_only_name_update_is_rejected(client, make_user, make_project, login_as):
    # Ruling R23: ProjectUpdate.name's min_length=1 only counts the raw input,
    # so "   " passes Pydantic but strips to "" with nothing to catch it the
    # way create_project's empty-slug check does — update_project never
    # touches the slug. A field_validator on ProjectUpdate.name closes this.
    owner = make_user(email="ada@example.com")
    project = make_project(owner, name="Payment Gateway")
    login_as("ada@example.com")
    response = client.patch(f"/api/v1/projects/{project.slug}", json={"name": "   "})
    assert response.status_code == 422
    # The project's name must be untouched by the rejected update.
    assert client.get(f"/api/v1/projects/{project.slug}").json()["name"] == "Payment Gateway"


def test_explicit_null_project_update_fields_are_ignored(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner, name="Payment Gateway")
    login_as(owner.email)

    response = client.patch(f"/api/v1/projects/{project.slug}", json={"name": None})

    assert response.status_code == 200
    assert response.json()["name"] == "Payment Gateway"
