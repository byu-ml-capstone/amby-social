from datetime import date

import pytest
from sqlmodel import Session, select

from app.models import (
    ReportAuthor,
    ReportGenerationStatus,
    Sprint,
    SprintReport,
    SprintStatus,
    Ticket,
    TicketStatus,
)
from app.report_grounding import ungrounded_facts
from app.services import close_sprint, create_ticket


@pytest.fixture
def closed_world(engine, make_user, make_project, add_member):
    """A closed sprint with one done ticket (KEY-1, 3 pts) and one rollover (KEY-2, 5 pts)."""
    owner = make_user(email="owner@example.com", name="Ada")
    member = make_user(email="member@example.com", name="Grace")
    outsider = make_user(email="outsider@example.com")
    project = make_project(owner)
    add_member(project, member)
    with Session(engine) as session:
        sprint = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 10, 2),
            committed_points=8,
        )
        next_sprint = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Next",
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 16),
        )
        session.add_all([sprint, next_sprint])
        session.commit()
        done = create_ticket(
            session, project, owner, title="Card form", story_points=3, sprint_id=sprint.id
        )
        open_ticket = create_ticket(
            session, project, owner, title="Refunds", story_points=5, sprint_id=sprint.id
        )
        for ticket, ticket_status in (
            (done, TicketStatus.DONE),
            (open_ticket, TicketStatus.IN_PROGRESS),
        ):
            ticket = session.get(Ticket, ticket.id)
            ticket.status = ticket_status
            session.add(ticket)
        session.commit()
        close_sprint(session, session.get(Sprint, sprint.id))
        return {
            "owner": owner,
            "member": member,
            "outsider": outsider,
            "project": project,
            "sprint_id": sprint.id,
            "next_sprint_id": next_sprint.id,
        }


def _report(engine, sprint_id):
    with Session(engine) as session:
        return session.exec(select(SprintReport).where(SprintReport.sprint_id == sprint_id)).one()


def test_closing_a_sprint_creates_one_pending_report(engine, closed_world):
    report = _report(engine, closed_world["sprint_id"])

    assert report.generation_status is ReportGenerationStatus.PENDING
    assert report.generated_by is None
    assert report.is_finalized is False


def test_members_read_the_report_but_outsiders_and_open_sprints_404(client, closed_world, login_as):
    login_as(closed_world["member"].email)
    response = client.get(f"/api/v1/sprints/{closed_world['sprint_id']}/report")
    assert response.status_code == 200
    assert response.json()["generation_status"] == "PENDING"

    assert client.get(f"/api/v1/sprints/{closed_world['next_sprint_id']}/report").status_code == 404

    login_as(closed_world["outsider"].email)
    assert client.get(f"/api/v1/sprints/{closed_world['sprint_id']}/report").status_code == 404


def test_payload_carries_the_facts_a_report_may_cite(client, closed_world, login_as):
    login_as(closed_world["member"].email)

    payload = client.get(f"/api/v1/sprints/{closed_world['sprint_id']}/report/payload").json()

    key = closed_world["project"].key
    assert payload["sprint"]["committed_points"] == 8
    assert payload["sprint"]["completed_points"] == 3
    assert payload["sprint"]["start_date"] == "2026-09-21"
    assert [ticket["ref"] for ticket in payload["tickets"]] == [f"{key}-1", f"{key}-2"]
    assert payload["totals"] == {
        "ticket_count": 2,
        "completed_count": 1,
        "rolled_over_count": 1,
        "completed_points": 3,
        "rolled_over_points": 5,
    }
    assert payload["recent_velocity"] == [
        {"name": "Sprint 1", "committed_points": 8, "completed_points": 3}
    ]


def test_member_saves_a_human_draft(client, engine, closed_world, login_as):
    login_as(closed_world["member"].email)

    response = client.put(
        f"/api/v1/sprints/{closed_world['sprint_id']}/report",
        json={"goal_summary": "Mostly there.", "faculty_notes": "Looks fine"},
    )

    assert response.status_code == 200
    report = _report(engine, closed_world["sprint_id"])
    assert report.goal_summary == "Mostly there."
    assert report.faculty_notes == "Looks fine"
    assert report.generated_by is ReportAuthor.HUMAN
    assert report.generation_status is ReportGenerationStatus.PENDING


def test_grounded_mcp_draft_is_saved_as_generated(client, engine, closed_world, login_as):
    login_as(closed_world["member"].email)
    key = closed_world["project"].key

    response = client.put(
        f"/api/v1/sprints/{closed_world['sprint_id']}/report",
        json={
            "generated_by": "MCP_CLIENT",
            "completed_summary": f"{key}-1 shipped (3 pts) on 2026-10-02.",
            "blockers_and_rollovers": f"{key}-2 rolled over with 5 points.",
            "velocity_summary": "Completed 3 of 8 pts.",
        },
    )

    assert response.status_code == 200, response.text
    report = _report(engine, closed_world["sprint_id"])
    assert report.generated_by is ReportAuthor.MCP_CLIENT
    assert report.generation_status is ReportGenerationStatus.OK


def test_ungrounded_mcp_draft_is_rejected_with_the_offending_facts(
    client, engine, closed_world, login_as
):
    login_as(closed_world["member"].email)
    key = closed_world["project"].key

    response = client.put(
        f"/api/v1/sprints/{closed_world['sprint_id']}/report",
        json={
            "generated_by": "MCP_CLIENT",
            "completed_summary": f"{key}-9 shipped (13 pts) on 2026-11-30.",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert f"{key}-9" in detail and "13 pts" in detail and "2026-11-30" in detail
    assert _report(engine, closed_world["sprint_id"]).completed_summary == ""


def test_ungrounded_facts_accepts_hash_refs_and_point_variants():
    payload = {
        "project": {"key": "PAY"},
        "sprint": {"start_date": "2026-09-21", "committed_points": 8},
        "tickets": [{"ref": "PAY-1", "ticket_number": 1, "story_points_at_close": 3}],
        "totals": {"completed_points": 3},
    }

    assert ungrounded_facts("#1 and PAY-1 took 3 points, 8 pt committed", payload) == []
    assert ungrounded_facts("#4 took 1 point", payload) == ["#4", "1 point"]


def test_only_owner_finalizes_and_finalized_reports_are_locked(client, closed_world, login_as):
    url = f"/api/v1/sprints/{closed_world['sprint_id']}/report"
    login_as(closed_world["member"].email)
    assert client.post(f"{url}/finalize").status_code == 403

    login_as(closed_world["owner"].email)
    response = client.post(f"{url}/finalize")
    assert response.status_code == 200
    assert response.json()["is_finalized"] is True
    assert response.json()["finalized_by"] == closed_world["owner"].id

    assert client.post(f"{url}/finalize").status_code == 409
    assert client.put(url, json={"goal_summary": "late edit"}).status_code == 409


def test_web_history_page_edits_and_finalizes_the_report(client, engine, closed_world, login_as):
    slug = closed_world["project"].slug
    sprint_id = closed_world["sprint_id"]
    login_as(closed_world["owner"].email)
    page = client.get(f"/projects/{slug}/sprints/{sprint_id}")
    assert page.status_code == 200
    assert "Sprint report" in page.text
    assert "get_sprint_report_payload" in page.text  # tells the user how to draft it
    csrf = page.text.split('name="_csrf" value="')[1].split('"')[0]

    saved = client.post(
        f"/projects/{slug}/sprints/{sprint_id}/report",
        data={"_csrf": csrf, "goal_summary": "Edited in the browser"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert _report(engine, sprint_id).goal_summary == "Edited in the browser"

    finalized = client.post(
        f"/projects/{slug}/sprints/{sprint_id}/report/finalize",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert finalized.status_code == 303
    assert _report(engine, sprint_id).is_finalized is True
    assert "Finalized" in client.get(f"/projects/{slug}/sprints/{sprint_id}").text


def test_web_finalize_is_owner_only(client, closed_world, login_as):
    slug = closed_world["project"].slug
    sprint_id = closed_world["sprint_id"]
    login_as(closed_world["member"].email)
    page = client.get(f"/projects/{slug}/sprints/{sprint_id}")
    csrf = page.text.split('name="_csrf" value="')[1].split('"')[0]
    assert "report/finalize" not in page.text

    response = client.post(
        f"/projects/{slug}/sprints/{sprint_id}/report/finalize",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_deleting_a_project_removes_its_reports(client, engine, closed_world, login_as):
    login_as(closed_world["owner"].email)
    slug = closed_world["project"].slug

    response = client.delete(f"/api/v1/projects/{slug}?confirm={slug}")

    assert response.status_code == 204
    with Session(engine) as session:
        assert session.exec(select(SprintReport)).all() == []


def test_a_report_finalized_mid_edit_rejects_the_edit(engine, closed_world):
    from fastapi import HTTPException

    from app.models import User
    from app.sprint_reports import finalize_report, save_report

    with Session(engine) as editor, Session(engine) as finalizer:
        sprint = editor.get(Sprint, closed_world["sprint_id"])
        stale = editor.exec(select(SprintReport).where(SprintReport.sprint_id == sprint.id)).one()
        finalize_report(
            finalizer,
            finalizer.get(SprintReport, stale.id),
            finalizer.get(User, closed_world["owner"].id),
        )

        with pytest.raises(HTTPException) as raised:
            save_report(editor, sprint, stale, {"goal_summary": "Late edit"})

    assert raised.value.status_code == 409
    assert _report(engine, closed_world["sprint_id"]).goal_summary != "Late edit"
