"""Dashboard, project creation and the sprint board."""

from datetime import date
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from sqlalchemy import case
from sqlmodel import Session, select

from app.auth import (
    current_user,
    load_project_and_membership,
    optional_user,
    project_reader,
    verify_csrf,
)
from app.db import get_session
from app.models import (
    Priority,
    Project,
    ProjectMember,
    Sprint,
    SprintStatus,
    Ticket,
    TicketStatus,
    TicketType,
    User,
)
from app.routers.web_common import COLUMNS, render
from app.services import create_project, sprint_chronology

router = APIRouter(tags=["web"])

MY_REQUESTS_LIMIT = 5
BOARD_TICKETS_PER_COLUMN = 200

# Hoisted so `Query(...)` isn't called in an argument default (ruff B008).
_TYPE_FILTER_QUERY = Query(None, alias="type")
_STATUS_FILTER_QUERY = Query(None, alias="status")
_START_DATE_FORM = Form("")


def _dashboard_context(session: Session, user: User) -> dict:
    today = date.today()
    open_rows = session.exec(
        select(Ticket, Project)
        .join(Project, Ticket.project_id == Project.id)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Ticket.assignee_id == user.id,
            ProjectMember.user_id == user.id,
            Ticket.status != TicketStatus.DONE,
        )
        .order_by(Ticket.due_date.is_(None), Ticket.due_date, Ticket.created_at.desc())
        .limit(100)
    ).all()
    recently_completed_tickets = session.exec(
        select(Ticket, Project)
        .join(Project, Ticket.project_id == Project.id)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Ticket.assignee_id == user.id,
            ProjectMember.user_id == user.id,
            Ticket.status == TicketStatus.DONE,
        )
        .order_by(Ticket.completed_at.desc())
        .limit(10)
    ).all()
    overdue_tickets = []
    in_progress_tickets = []
    next_tickets = []
    for row in open_rows:
        ticket = row[0]
        if ticket.due_date is not None and ticket.due_date < today:
            overdue_tickets.append(row)
        elif ticket.status is TicketStatus.IN_PROGRESS:
            in_progress_tickets.append(row)
        else:
            next_tickets.append(row)
    return {
        "user": user,
        "today": today,
        "overdue_tickets": overdue_tickets,
        "in_progress_tickets": in_progress_tickets,
        "next_tickets": next_tickets,
        "recently_completed_tickets": recently_completed_tickets,
    }


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "dashboard.html", _dashboard_context(session, user), session=session)


@router.post("/projects", dependencies=[Depends(verify_csrf)])
def create_project_form(
    request: Request,
    name: str = Form(...),
    start_date: str = _START_DATE_FORM,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    # create_project (app/services.py) is the same function the JSON route
    # (POST /api/v1/projects) calls -- slug derivation, the name limits
    # (Ruling R36, since this form bypasses ProjectCreate entirely), the slug
    # collision check, and the creator-becomes-OWNER membership insert all
    # live there exactly once.
    try:
        project_date = date.fromisoformat(start_date) if start_date else date.today()
        project = create_project(session, name, project_date, user)
    except ValueError:
        context = _dashboard_context(session, user)
        context.update(
            {"error": "Enter a valid start date", "name": name, "start_date": start_date}
        )
        return render(
            request,
            "dashboard.html",
            context,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            session=session,
        )
    except HTTPException as exc:
        context = _dashboard_context(session, user)
        context.update({"error": exc.detail, "name": name, "start_date": start_date})
        return render(
            request,
            "dashboard.html",
            context,
            status_code=exc.status_code,
            session=session,
        )
    return RedirectResponse(f"/projects/{project.slug}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{slug}")
def board(
    slug: str,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
    sprint_id: str | None = None,
    mine: bool = False,
    assignee_id: str | None = None,
    type_filter: str | None = _TYPE_FILTER_QUERY,
    priority: str | None = None,
) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    # load_project_and_membership already 404s when the project itself is missing; a non-member
    # must get the same 404 rather than learn the project exists, so check member too.
    project, member = load_project_and_membership(slug, user, session)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    try:
        type_filter = TicketType(type_filter) if type_filter else None
        priority = Priority(priority) if priority else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid board filter") from exc

    sprint = session.exec(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.ACTIVE,
        )
    ).first()
    if sprint is None:
        sprint = session.exec(
            select(Sprint)
            .where(
                Sprint.project_id == project.id,
                Sprint.status == SprintStatus.PLANNING,
            )
            .order_by(*sprint_chronology())
        ).first()
    if sprint is None:
        return RedirectResponse(
            f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
        )
    if sprint_id:
        sprint = session.exec(
            select(Sprint).where(
                Sprint.id == sprint_id,
                Sprint.project_id == project.id,
                Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
            )
        ).first()
        if sprint is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")

    sprint_summary = None
    if sprint.status is SprintStatus.ACTIVE:
        summary_tickets = session.exec(select(Ticket).where(Ticket.sprint_id == sprint.id)).all()
        done = [ticket for ticket in summary_tickets if ticket.status is TicketStatus.DONE]
        total = len(summary_tickets)
        sprint_summary = {
            "completed_count": len(done),
            "total_count": total,
            "completion_percent": round(len(done) * 100 / total) if total else 0,
            "completed_points": sum(ticket.story_points or 0 for ticket in done),
            "total_points": sum(ticket.story_points or 0 for ticket in summary_tickets),
            "rollover_count": sum(ticket.rollover_count > 0 for ticket in summary_tickets),
            "at_risk_count": sum(
                bool(ticket.blocked_reason)
                or (ticket.due_date is not None and ticket.due_date < date.today())
                or ticket.rollover_count >= 2
                or (ticket.delayed_days or 0) >= 14
                for ticket in summary_tickets
                if ticket.status is not TicketStatus.DONE
            ),
        }

    filters = [Ticket.project_id == project.id, Ticket.sprint_id == sprint.id]
    if mine:
        filters.append(Ticket.assignee_id == user.id)
    if assignee_id:
        filters.append(Ticket.assignee_id == assignee_id)
    if type_filter:
        filters.append(Ticket.type == type_filter)
    if priority:
        filters.append(Ticket.priority == priority)

    tickets_by_status = {}
    truncated_columns = set()
    for column in COLUMNS:
        tickets = session.exec(
            select(Ticket)
            .where(*filters, Ticket.status == column)
            .order_by(Ticket.ticket_number.desc())
            .limit(BOARD_TICKETS_PER_COLUMN + 1)
        ).all()
        if len(tickets) > BOARD_TICKETS_PER_COLUMN:
            truncated_columns.add(column.value)
        tickets_by_status[column.value] = tickets[:BOARD_TICKETS_PER_COLUMN]
    # Tickets this user filed, under the same board filters, open ones first.
    my_requests = session.exec(
        select(Ticket)
        .where(*filters, Ticket.creator_id == user.id)
        .order_by((Ticket.status == TicketStatus.DONE), Ticket.ticket_number.desc())
        .limit(MY_REQUESTS_LIMIT)
    ).all()
    members = session.exec(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.name)
    ).all()
    return render(
        request,
        "board.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "board",
            "sprint": sprint,
            "sprint_summary": sprint_summary,
            "selected_sprint_id": sprint.id,
            "columns": COLUMNS,
            "tickets_by_status": tickets_by_status,
            "my_requests": my_requests,
            "truncated_columns": truncated_columns,
            "mine": mine,
            "assignee_id": assignee_id,
            "type_filter": type_filter,
            "priority_filter": priority,
            "ticket_types": TicketType,
            "priorities": Priority,
            "members": members,
            "assignee_names": {member.id: member.name or member.email for member in members},
            "destination_sprints": session.exec(
                select(Sprint)
                .where(
                    Sprint.project_id == project.id,
                    Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
                )
                .order_by(*sprint_chronology())
            ).all(),
            "today": date.today(),
        },
        session=session,
    )


LIST_TICKET_LIMIT = 200
_PRIORITY_RANK = case(
    {Priority.URGENT: 0, Priority.HIGH: 1, Priority.MEDIUM: 2, Priority.LOW: 3},
    value=Ticket.priority,
)
# Newest first is the tie-breaker for every sort.
_LIST_SORTS = {
    "number": (),
    "priority": (_PRIORITY_RANK,),
    "due": (Ticket.due_date.is_(None), Ticket.due_date),
}


@router.get("/projects/{slug}/list")
def ticket_list(
    request: Request,
    q: str = "",
    status_filter: TicketStatus | None = _STATUS_FILTER_QUERY,
    type_filter: TicketType | None = _TYPE_FILTER_QUERY,
    priority: Priority | None = None,
    assignee: str = "",
    sprint: str = "",
    sort: Literal["number", "priority", "due"] = "number",
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    filters = [Ticket.project_id == project.id]
    if q.strip():
        filters.append(Ticket.title.ilike(f"%{q.strip()}%"))
    if status_filter:
        filters.append(Ticket.status == status_filter)
    if type_filter:
        filters.append(Ticket.type == type_filter)
    if priority:
        filters.append(Ticket.priority == priority)
    if assignee == "me":
        filters.append(Ticket.assignee_id == user.id)
    elif assignee == "none":
        filters.append(Ticket.assignee_id.is_(None))
    elif assignee:
        filters.append(Ticket.assignee_id == assignee)
    if sprint == "none":
        filters.append(Ticket.sprint_id.is_(None))
    elif sprint:
        filters.append(Ticket.sprint_id == sprint)

    tickets = session.exec(
        select(Ticket)
        .where(*filters)
        .order_by(*_LIST_SORTS[sort], Ticket.ticket_number.desc())
        .limit(LIST_TICKET_LIMIT + 1)
    ).all()
    members = session.exec(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.name)
    ).all()
    sprints = session.exec(
        select(Sprint).where(Sprint.project_id == project.id).order_by(*sprint_chronology())
    ).all()
    return render(
        request,
        "ticket_list.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "list",
            "tickets": tickets[:LIST_TICKET_LIMIT],
            "truncated": len(tickets) > LIST_TICKET_LIMIT,
            "members": members,
            "assignee_names": {row.id: row.name or row.email for row in members},
            "sprints": sprints,
            "sprint_names": {row.id: row.name for row in sprints},
            "statuses": COLUMNS,
            "ticket_types": TicketType,
            "priorities": Priority,
            "q": q,
            "status_filter": status_filter,
            "type_filter": type_filter,
            "priority_filter": priority,
            "assignee": assignee,
            "sprint_filter": sprint,
            "sort": sort,
            "today": date.today(),
        },
        session=session,
    )
