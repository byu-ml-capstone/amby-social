from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, select

from app import attachments
from app.auth import current_user, load_project_and_membership, project_reader, require_member
from app.db import get_session
from app.github_sync import branch_command, ticket_development
from app.models import (
    Priority,
    Project,
    ProjectMember,
    Role,
    Ticket,
    TicketAttachment,
    TicketStatus,
    TicketType,
    User,
)
from app.schemas import (
    AttachmentOut,
    DevelopmentOut,
    StatusUpdate,
    TicketCreate,
    TicketDetailOut,
    TicketOut,
    TicketPage,
    TicketUpdate,
)
from app.services import create_ticket, delete_ticket_record, set_status, update_ticket

router = APIRouter(prefix="/api/v1", tags=["tickets"])

_STATUS_QUERY = Query(default=None, alias="status")
_TYPE_QUERY = Query(default=None, alias="type")
_LIMIT_QUERY = Query(default=50, ge=1, le=200)


def _project_for_write(slug: str, user: User, session: Session) -> Project:
    project, member = load_project_and_membership(slug, user, session)
    require_member(member)
    return project


def load_ticket_for_read(
    ticket_id: str, user: User, session: Session
) -> tuple[Ticket, Project, ProjectMember]:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == ticket.project_id, ProjectMember.user_id == user.id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket, session.get(Project, ticket.project_id), member


def load_ticket_for_write(
    ticket_id: str, user: User, session: Session
) -> tuple[Ticket, Project, ProjectMember]:
    # Same shape as load_ticket_for_read, but a non-member gets 403 here, not
    # 404 -- a write must not silently no-op behind a "not found" the way a
    # read is allowed to. A missing ticket is still a genuine 404: that's not
    # a membership question. require_member is the same status decision
    # project_writer uses, so there is one place -- not a third -- deciding
    # what a non-member gets on a write.
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == ticket.project_id, ProjectMember.user_id == user.id
        )
    ).first()
    require_member(member)
    return ticket, session.get(Project, ticket.project_id), member


@router.post("/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def post_ticket(
    body: TicketCreate,
    tasks: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Ticket:
    project = _project_for_write(body.slug, user, session)
    return create_ticket(
        session,
        project,
        user,
        title=body.title,
        description=body.description,
        type=body.type,
        priority=body.priority,
        story_points=body.story_points,
        due_date=body.due_date,
        sprint_id=body.sprint_id,
        assignee_id=body.assignee_id,
        meta=body.meta,
        tasks=tasks,
    )


@router.get("/projects/{slug}/tickets", response_model=TicketPage)
def list_tickets(
    status_filter: TicketStatus | None = _STATUS_QUERY,
    assignee_id: str | None = None,
    type_filter: TicketType | None = _TYPE_QUERY,
    priority: Priority | None = None,
    sprint_id: str | None = None,
    limit: int = _LIMIT_QUERY,
    cursor: int | None = None,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    session: Session = Depends(get_session),
) -> TicketPage:
    project, _ = access
    query = select(Ticket).where(Ticket.project_id == project.id)
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    if assignee_id:
        query = query.where(Ticket.assignee_id == assignee_id)
    if type_filter:
        query = query.where(Ticket.type == type_filter)
    if priority:
        query = query.where(Ticket.priority == priority)
    if sprint_id == "null":
        query = query.where(Ticket.sprint_id.is_(None))
    elif sprint_id is not None:
        query = query.where(Ticket.sprint_id == sprint_id)
    if cursor is not None:
        query = query.where(Ticket.ticket_number < cursor)
    rows = session.exec(query.order_by(Ticket.ticket_number.desc()).limit(limit)).all()
    next_cursor = rows[-1].ticket_number if len(rows) == limit else None
    return TicketPage(items=rows, next_cursor=next_cursor)


@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut)
def get_ticket(
    ticket_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TicketDetailOut:
    ticket, project, _ = load_ticket_for_read(ticket_id, user, session)
    rows = session.exec(
        select(TicketAttachment)
        .where(TicketAttachment.ticket_id == ticket.id)
        .order_by(TicketAttachment.created_at)
    ).all()
    return TicketDetailOut.model_validate(ticket).model_copy(
        update={
            "attachments": [AttachmentOut.model_validate(row) for row in rows],
            "branch_command": branch_command(project, ticket),
            "development": [
                DevelopmentOut.model_validate(row) for row in ticket_development(session, ticket)
            ],
        }
    )


@router.get("/attachments/{attachment_id}")
def get_attachment_content(
    attachment_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    row = session.get(TicketAttachment, attachment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    load_ticket_for_read(row.ticket_id, user, session)  # non-members get the same 404
    return attachments.file_response(row)


@router.patch("/tickets/{ticket_id}", response_model=TicketOut)
def patch_ticket(
    ticket_id: str,
    body: TicketUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Ticket:
    ticket, project, _ = load_ticket_for_write(ticket_id, user, session)
    data = body.model_dump(exclude_unset=True)
    return update_ticket(session, ticket, project, **data)


@router.patch("/tickets/{ticket_id}/status", response_model=TicketOut)
def patch_status(
    ticket_id: str,
    body: StatusUpdate,
    tasks: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Ticket:
    ticket, project, _ = load_ticket_for_write(ticket_id, user, session)
    return set_status(
        session, ticket, body.status, body.resolution_notes, project=project, tasks=tasks
    )


@router.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(
    ticket_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> None:
    ticket, _, member = load_ticket_for_write(ticket_id, user, session)
    if member.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
    delete_ticket_record(session, ticket)
