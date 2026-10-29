from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session, select

from app.auth import current_user, project_owner, project_reader, project_writer, verify_csrf
from app.db import get_session
from app.models import (
    REPORT_TEXT_MAX_LENGTH,
    Priority,
    Project,
    ProjectMember,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
    TicketType,
    User,
)
from app.routers.web_common import render
from app.schemas import SprintCreate
from app.services import (
    close_sprint,
    create_sprints,
    plan_backlog_tickets,
    reorder_backlog_ticket,
    sprint_chronology,
    start_sprint,
    update_sprint,
)
from app.sprint_reports import REPORT_FIELDS, finalize_report, get_report, save_report

router = APIRouter(tags=["web"])
_TICKET_IDS_FORM = Form(...)


def _backlog(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    member: ProjectMember,
    status_code: int = status.HTTP_200_OK,
    q: str = "",
    priority: Priority | None = None,
    type_filter: TicketType | None = None,
    **context,
) -> Response:
    planning_sprints = session.exec(
        select(Sprint)
        .where(
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.PLANNING,
        )
        .order_by(*sprint_chronology())
    ).all()
    filters = [Ticket.project_id == project.id, Ticket.sprint_id.is_(None)]
    if q:
        filters.append(Ticket.title.ilike(f"%{q.strip()}%"))
    if priority:
        filters.append(Ticket.priority == priority)
    if type_filter:
        filters.append(Ticket.type == type_filter)
    tickets = session.exec(
        select(Ticket).where(*filters).order_by(Ticket.backlog_rank.desc())
    ).all()
    planning_groups = []
    for planning_sprint in planning_sprints:
        sprint_tickets = session.exec(
            select(Ticket).where(Ticket.sprint_id == planning_sprint.id)
        ).all()
        planning_groups.append(
            {
                "sprint": planning_sprint,
                "tickets": sprint_tickets,
                "ticket_count": len(sprint_tickets),
                "points": sum(ticket.story_points or 0 for ticket in sprint_tickets),
                "unestimated_count": sum(ticket.story_points is None for ticket in sprint_tickets),
            }
        )
    planning_sprint = planning_sprints[0] if planning_sprints else None
    planning_tickets = planning_groups[0]["tickets"] if planning_groups else []
    recent_completed = session.exec(
        select(Sprint.completed_points)
        .where(Sprint.project_id == project.id, Sprint.status == SprintStatus.CLOSED)
        .order_by(Sprint.end_date.desc())
        .limit(3)
    ).all()
    recent_velocity = (
        round(sum(points or 0 for points in recent_completed) / len(recent_completed))
        if recent_completed
        else None
    )
    return render(
        request,
        "backlog.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "backlog",
            "planning_sprint": planning_sprint,
            "planning_sprints": planning_sprints,
            "planning_groups": planning_groups,
            "planning_tickets": planning_tickets,
            "planning_ticket_count": len(planning_tickets),
            "planning_points": sum(ticket.story_points or 0 for ticket in planning_tickets),
            "planning_unestimated_count": sum(
                ticket.story_points is None for ticket in planning_tickets
            ),
            "recent_velocity": recent_velocity,
            "tickets": tickets,
            "q": q,
            "priority_filter": priority,
            "type_filter": type_filter,
            "priorities": Priority,
            "ticket_types": TicketType,
            "sprint": None,
            "destination_sprints": session.exec(
                select(Sprint)
                .where(
                    Sprint.project_id == project.id,
                    Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
                )
                .order_by(*sprint_chronology())
            ).all(),
            "values": {},
            "field_errors": {},
            "error": None,
            **context,
        },
        status_code=status_code,
        session=session,
    )


def _project_sprint(session: Session, project: Project, sprint_id: str) -> Sprint:
    sprint = session.exec(
        select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project.id)
    ).first()
    if sprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")
    return sprint


def _closed_sprint(session: Session, project: Project, sprint_id: str) -> Sprint:
    sprint = _project_sprint(session, project, sprint_id)
    if sprint.status != SprintStatus.CLOSED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")
    return sprint


def _history_rows(session: Session, sprint_ids: list[str]):
    if not sprint_ids:
        return []
    return session.exec(
        select(SprintTicketHistory, Ticket)
        .join(Ticket, Ticket.id == SprintTicketHistory.ticket_id)
        .where(SprintTicketHistory.sprint_id.in_(sprint_ids))
        .order_by(Ticket.ticket_number)
    ).all()


def _times_rolled_over(
    session: Session, sprints: list[Sprint], rows: list[tuple[SprintTicketHistory, Ticket]]
) -> dict[tuple[str, str], int]:
    """How many times each unfinished ticket had rolled over as of that sprint's close.

    Counted from history rather than Ticket.rollover_count, so a closed sprint reads the
    same after later sprints close.
    """
    end_dates = {sprint.id: sprint.end_date for sprint in sprints}
    ticket_ids = {history.ticket_id for history, _ in rows if not history.was_completed}
    if not ticket_ids:
        return {}
    misses = session.exec(
        select(SprintTicketHistory.ticket_id, Sprint.end_date)
        .join(Sprint, Sprint.id == SprintTicketHistory.sprint_id)
        .where(
            SprintTicketHistory.ticket_id.in_(ticket_ids),
            SprintTicketHistory.was_completed.is_(False),
        )
    ).all()
    return {
        (history.sprint_id, history.ticket_id): sum(
            ticket_id == history.ticket_id and end_date <= end_dates[history.sprint_id]
            for ticket_id, end_date in misses
        )
        for history, _ in rows
        if not history.was_completed
    }


def _repeat_rollovers(sprint: Sprint, times: dict[tuple[str, str], int]) -> int:
    # Same >= 2 threshold as the board's stale badge.
    return sum(count >= 2 for (sprint_id, _), count in times.items() if sprint_id == sprint.id)


def _close_preview(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    sprint: Sprint,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    tickets = session.exec(select(Ticket).where(Ticket.sprint_id == sprint.id)).all()
    return render(
        request,
        "partials/sprint_close.html",
        {
            "user": user,
            "project": project,
            "sprint": sprint,
            "completed_points": sum(
                ticket.story_points or 0 for ticket in tickets if ticket.status is TicketStatus.DONE
            ),
            "unfinished_count": sum(ticket.status is not TicketStatus.DONE for ticket in tickets),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/projects/{slug}/backlog")
def backlog(
    request: Request,
    q: str = "",
    priority: Priority | None = None,
    type: TicketType | None = None,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    return _backlog(
        request, session, user, project, member, q=q, priority=priority, type_filter=type
    )


@router.post("/projects/{slug}/backlog/{ticket_id}/move", dependencies=[Depends(verify_csrf)])
def move_backlog_ticket(
    ticket_id: str,
    direction: str = Form(...),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    reorder_backlog_ticket(session, project, ticket_id, direction)
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/projects/{slug}/sprints")
def sprint_history(
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    sprints = session.exec(
        select(Sprint)
        .where(Sprint.project_id == project.id, Sprint.status == SprintStatus.CLOSED)
        .order_by(Sprint.closed_at.desc(), Sprint.end_date.desc())
    ).all()
    rows = _history_rows(session, [sprint.id for sprint in sprints])
    rows_by_sprint = {sprint.id: [] for sprint in sprints}
    for history, ticket in rows:
        rows_by_sprint[history.sprint_id].append((history, ticket))
    times = _times_rolled_over(session, sprints, rows)
    summaries = [
        {
            "sprint": sprint,
            "rollover_count": sum(
                not history.was_completed for history, _ in rows_by_sprint[sprint.id]
            ),
            "completed_count": sum(
                history.was_completed for history, _ in rows_by_sprint[sprint.id]
            ),
            "repeat_rollovers": _repeat_rollovers(sprint, times),
        }
        for sprint in sprints
    ]
    return render(
        request,
        "sprint_history.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "history",
            "summaries": summaries,
            "velocity_points": [sprint.completed_points or 0 for sprint in reversed(sprints[:5])],
        },
        session=session,
    )


@router.get("/projects/{slug}/sprints/{sprint_id}")
def sprint_history_detail(
    sprint_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    sprint = _closed_sprint(session, project, sprint_id)
    rows = _history_rows(session, [sprint.id])
    times = _times_rolled_over(session, [sprint], rows)
    return render(
        request,
        "sprint_history_detail.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "history",
            "sprint": sprint,
            "selected_sprint_id": sprint.id,
            "history": rows,
            "rollover_count": sum(not entry.was_completed for entry, _ in rows),
            "repeat_rollovers": _repeat_rollovers(sprint, times),
            "times_rolled_over": times,
            "report": get_report(session, sprint),
            "report_fields": REPORT_FIELDS,
        },
        session=session,
    )


@router.post("/projects/{slug}/sprints/{sprint_id}/report", dependencies=[Depends(verify_csrf)])
async def save_sprint_report_form(
    sprint_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    sprint = _closed_sprint(session, project, sprint_id)
    form = await request.form()
    changes = {
        field: str(form[field])[:REPORT_TEXT_MAX_LENGTH]
        for field, _ in REPORT_FIELDS
        if field in form
    }
    save_report(session, sprint, get_report(session, sprint), changes)
    return RedirectResponse(
        f"/projects/{project.slug}/sprints/{sprint.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/projects/{slug}/sprints/{sprint_id}/report/finalize", dependencies=[Depends(verify_csrf)]
)
def finalize_sprint_report_form(
    sprint_id: str,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    sprint = _closed_sprint(session, project, sprint_id)
    finalize_report(session, get_report(session, sprint), user)
    return RedirectResponse(
        f"/projects/{project.slug}/sprints/{sprint.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{slug}/sprints", dependencies=[Depends(verify_csrf)])
def create_sprint_form(
    request: Request,
    count: int = Form(1),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    values = {"count": count}
    try:
        sprint_data = SprintCreate(**values)
        create_sprints(session, project, sprint_data.count)
    except ValidationError as exc:
        field_errors = {
            error["loc"][-1] if error["loc"] else "form": error["msg"] for error in exc.errors()
        }
        return _backlog(
            request,
            session,
            user,
            project,
            member,
            values=values,
            field_errors=field_errors,
            sprint_form_open=True,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except HTTPException as exc:
        return _backlog(
            request,
            session,
            user,
            project,
            member,
            values=values,
            error=exc.detail,
            sprint_form_open=True,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{slug}/sprints/{sprint_id}/goal", dependencies=[Depends(verify_csrf)])
def update_sprint_goal_form(
    sprint_id: str,
    goal: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    update_sprint(session, _project_sprint(session, project, sprint_id), goal=goal)
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{slug}/sprints/{sprint_id}/start", dependencies=[Depends(verify_csrf)])
def start_sprint_form(
    sprint_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    sprint = _project_sprint(session, project, sprint_id)
    try:
        start_sprint(session, sprint)
    except HTTPException as exc:
        return _backlog(
            request,
            session,
            user,
            project,
            member,
            start_error=exc.detail,
            start_form_open=True,
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/projects/{project.slug}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{slug}/sprints/{sprint_id}/close")
def close_sprint_preview(
    sprint_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    return _close_preview(
        request, session, user, project, _project_sprint(session, project, sprint_id)
    )


@router.post("/projects/{slug}/sprints/{sprint_id}/close", dependencies=[Depends(verify_csrf)])
def close_sprint_form(
    sprint_id: str,
    request: Request,
    goal_achieved: str = Form(""),
    review_notes: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    sprint = _project_sprint(session, project, sprint_id)
    try:
        review = {}
        if goal_achieved in {"yes", "no"}:
            review["goal_achieved"] = goal_achieved == "yes"
        if review_notes:
            review["review_notes"] = review_notes
        close_sprint(session, sprint, **review)
    except HTTPException as exc:
        return _close_preview(
            request,
            session,
            user,
            project,
            sprint,
            error=exc.detail,
            status_code=exc.status_code,
        )
    target = f"/projects/{project.slug}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{slug}/sprints/{sprint_id}/tickets", dependencies=[Depends(verify_csrf)])
def assign_tickets_to_planning_sprint(
    sprint_id: str,
    ticket_ids: list[str] = _TICKET_IDS_FORM,
    access: tuple[Project, ProjectMember] = Depends(project_writer),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    plan_backlog_tickets(session, project, sprint_id, ticket_ids)
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )
