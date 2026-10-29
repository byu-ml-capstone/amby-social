"""AI sprint reports (FR-13), MCP-first: the caller's own model drafts the report from
build_payload() and saves it back; the server only checks the draft is grounded."""

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import begin_write
from app.errors import Conflict, Invalid, NotFound
from app.models import (
    Project,
    ReportAuthor,
    ReportGenerationStatus,
    Sprint,
    SprintReport,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    User,
    utcnow,
)
from app.report_grounding import ungrounded_facts

REPORT_FIELDS = (
    ("goal_summary", "Goal"),
    ("completed_summary", "Completed"),
    ("blockers_and_rollovers", "Blockers and rollovers"),
    ("velocity_summary", "Velocity"),
    ("faculty_notes", "Reviewer notes"),
)


def get_report(session: Session, sprint: Sprint) -> SprintReport:
    """The sprint's report; 404 until it closes. Close creates the draft, and this fills in
    any closed sprint that still lacks one."""
    query = select(SprintReport).where(SprintReport.sprint_id == sprint.id)
    report = session.exec(query).first()
    if report is not None:
        return report
    if sprint.status is not SprintStatus.CLOSED:
        raise NotFound("Sprint report not found")
    session.add(SprintReport(sprint_id=sprint.id))
    try:
        session.commit()
    except IntegrityError:  # a concurrent request created it first
        session.rollback()
    return session.exec(query).one()


def build_payload(session: Session, sprint: Sprint) -> dict:
    """Everything a report may cite. Doubles as the grounding reference."""
    project = session.get(Project, sprint.project_id)
    rows = session.exec(
        select(SprintTicketHistory, Ticket, User)
        .join(Ticket, Ticket.id == SprintTicketHistory.ticket_id)
        .outerjoin(User, User.id == Ticket.assignee_id)
        .where(SprintTicketHistory.sprint_id == sprint.id)
        .order_by(Ticket.ticket_number)
    ).all()
    tickets = [
        {
            "ref": f"{project.key}-{ticket.ticket_number}",
            "ticket_number": ticket.ticket_number,
            "title": ticket.title,
            "type": ticket.type.value,
            "priority": ticket.priority.value,
            "status_at_close": history.status_at_close.value,
            "story_points_at_close": history.story_points_at_close,
            "was_completed": history.was_completed,
            "assignee": assignee.name if assignee else None,
            "blocked_reason": ticket.blocked_reason,
            "rollover_count": ticket.rollover_count,
            "completed_on": ticket.completed_at.date().isoformat() if ticket.completed_at else None,
        }
        for history, ticket, assignee in rows
    ]
    recent = session.exec(
        select(Sprint)
        .where(
            Sprint.project_id == sprint.project_id,
            Sprint.status == SprintStatus.CLOSED,
            Sprint.closed_at <= sprint.closed_at,
        )
        .order_by(Sprint.closed_at.desc())
        .limit(5)
    ).all()
    return {
        "project": {"name": project.name, "key": project.key},
        "sprint": {
            "name": sprint.name,
            "goal": sprint.goal,
            "start_date": sprint.start_date.isoformat(),
            "end_date": sprint.end_date.isoformat(),
            "closed_on": sprint.closed_at.date().isoformat() if sprint.closed_at else None,
            "committed_points": sprint.committed_points or 0,
            "completed_points": sprint.completed_points or 0,
            "goal_achieved": sprint.goal_achieved,
            "review_notes": sprint.review_notes,
        },
        "tickets": tickets,
        "totals": {
            "ticket_count": len(tickets),
            "completed_count": sum(t["was_completed"] for t in tickets),
            "rolled_over_count": sum(not t["was_completed"] for t in tickets),
            "completed_points": sum(
                t["story_points_at_close"] or 0 for t in tickets if t["was_completed"]
            ),
            "rolled_over_points": sum(
                t["story_points_at_close"] or 0 for t in tickets if not t["was_completed"]
            ),
        },
        "recent_velocity": [
            {
                "name": past.name,
                "committed_points": past.committed_points or 0,
                "completed_points": past.completed_points or 0,
            }
            for past in reversed(recent)
        ],
    }


def save_report(
    session: Session,
    sprint: Sprint,
    report: SprintReport,
    changes: dict[str, str],
    author: ReportAuthor = ReportAuthor.HUMAN,
) -> SprintReport:
    _lock_unfinalized(session, report)
    if author is ReportAuthor.MCP_CLIENT:
        missing = ungrounded_facts("\n".join(changes.values()), build_payload(session, sprint))
        if missing:
            raise Invalid(
                "Draft cites facts not in the sprint report payload: "
                + ", ".join(missing)
                + ". Use only values from get_sprint_report_payload.",
            )
        report.generated_by = author
        report.generation_status = ReportGenerationStatus.OK
    elif report.generated_by is None:
        report.generated_by = ReportAuthor.HUMAN
    for field, value in changes.items():
        setattr(report, field, value)
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def _lock_unfinalized(session: Session, report: SprintReport) -> None:
    # Re-read under the write lock: the copy in hand may predate a finalize that just committed.
    begin_write(session)
    session.refresh(report)
    if report.is_finalized:
        raise Conflict("Sprint report is finalized")


def finalize_report(session: Session, report: SprintReport, user: User) -> SprintReport:
    _lock_unfinalized(session, report)
    report.is_finalized = True
    report.finalized_by = user.id
    report.finalized_at = utcnow()
    session.add(report)
    session.commit()
    session.refresh(report)
    return report
