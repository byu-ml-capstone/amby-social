"""Ticket pages: detail, edit, status, comments and attachments."""

import json
from datetime import date
from urllib.parse import parse_qs, urlparse

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session, select

from app import attachments, comments
from app.auth import (
    current_user,
    project_owner,
    project_reader,
    project_writer,
    verify_csrf,
)
from app.db import get_session
from app.github_sync import branch_command, ticket_development, ticket_reference
from app.models import (
    Priority,
    Project,
    ProjectMember,
    Sprint,
    SprintStatus,
    Subtask,
    SubtaskStatus,
    Ticket,
    TicketAttachment,
    TicketComment,
    TicketStatus,
    TicketType,
    User,
    utcnow,
)
from app.routers.web_common import COLUMNS, render
from app.schemas import StatusUpdate, TicketUpdate
from app.services import (
    create_ticket,
    delete_ticket_record,
    set_status,
    sprint_chronology,
    update_ticket,
    create_subtask,
    delete_subtask_record,
    update_subtask,
)

router = APIRouter(tags=["web"])


# Hoisted so `Form(...)` isn't called in an argument default (ruff B008).
_DEFAULT_TICKET_TYPE = Form(TicketType.TASK)
_STATUS_FORM_FIELD = Form(..., alias="status")
_MENTION_IDS_FORM = Form(None)
_FILES_FORM = File(...)
_DUE_DATE_FORM = Form(None)


def _project_ticket(session: Session, project: Project, ticket_number: int) -> Ticket:
    ticket = session.exec(
        select(Ticket).where(Ticket.project_id == project.id, Ticket.ticket_number == ticket_number)
    ).first()
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


def _subtask_context(session: Session, ticket: Ticket) -> dict:
    subtasks = session.exec(
        select(Subtask)
        .where(Subtask.ticket_id == ticket.id)
        .order_by(Subtask.position, Subtask.created_at)
    ).all()
    completed = sum(
        getattr(subtask.status, "value", subtask.status) == SubtaskStatus.DONE.value
        for subtask in subtasks
    )
    return {
        "subtasks": subtasks,
        "subtask_statuses": SubtaskStatus,
        "subtask_completed": completed,
        "subtask_percent": round(completed * 100 / len(subtasks)) if subtasks else 0,
    }


def _ticket_comments(
    session: Session, project: Project, ticket: Ticket, viewer: User
) -> tuple[list[dict], list[User]]:
    members = session.exec(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.name)
    ).all()
    viewer_membership = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == viewer.id,
        )
    ).one()
    names = {member.id: member.name or member.email for member in members}
    rows = session.exec(
        select(TicketComment, User)
        .join(User, User.id == TicketComment.author_id)
        .where(TicketComment.ticket_id == ticket.id)
        .order_by(TicketComment.created_at)
    ).all()
    return (
        [
            {
                "comment": comment,
                "author_name": author.name or author.email,
                "mention_names": [
                    names[user_id]
                    for user_id in comment.mentioned_user_ids
                    if user_id in names and f"@{names[user_id]}" not in comment.body
                ],
                "can_edit": comments.can_edit(comment, viewer),
                "can_delete": comments.can_delete(comment, viewer, viewer_membership.role),
            }
            for comment, author in rows
        ],
        members,
    )


def _render_ticket_comments(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    ticket: Ticket,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    comments, members = _ticket_comments(session, project, ticket, user)
    return render(
        request,
        "partials/ticket_comments.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            "comments": comments,
            "members": members,
            "comment_error": error,
        },
        status_code=status_code,
    )


def _attachment_context(
    session: Session, project: Project, ticket: Ticket, viewer: User, error: str | None = None
) -> dict:
    role = session.exec(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == viewer.id
        )
    ).one()
    rows = session.exec(
        select(TicketAttachment)
        .where(TicketAttachment.ticket_id == ticket.id)
        .order_by(TicketAttachment.created_at)
    ).all()
    items = [
        {
            "attachment": row,
            "url": f"/projects/{project.slug}/attachments/{row.id}",
            "delete_url": (
                f"/projects/{project.slug}/tickets/{ticket.ticket_number}"
                f"/attachments/{row.id}/delete"
            ),
            "can_delete": attachments.can_delete(row, viewer, role),
        }
        for row in rows
    ]
    return {
        "attachment_images": [item for item in items if item["attachment"].is_image],
        "attachment_files": [item for item in items if not item["attachment"].is_image],
        "attachment_error": error,
        "attachment_max_bytes": attachments.ATTACHMENT_MAX_BYTES,
    }


def _render_ticket_attachments(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    ticket: Ticket,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return render(
        request,
        "partials/ticket_attachments.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            **_attachment_context(session, project, ticket, user, error),
        },
        status_code=status_code,
    )


def _ticket_detail(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    ticket: Ticket,
    *,
    error: str | None = None,
    comment_error: str | None = None,
    status_code: int = status.HTTP_200_OK,
    page: bool = False,
    submitted_values: dict[str, str] | None = None,
    saved: bool = False,
) -> Response:
    comments, members = _ticket_comments(session, project, ticket, user)
    reference = ticket_reference(project, ticket)
    command = branch_command(project, ticket)
    development = ticket_development(session, ticket)
    subtask_context = _subtask_context(session, ticket)
    sprints = session.exec(
        select(Sprint)
        .where(
            Sprint.project_id == project.id,
            Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
        )
        .order_by(*sprint_chronology())
    ).all()
    form_values = {
        "title": ticket.title,
        "description": ticket.description,
        "type": ticket.type.value,
        "priority": ticket.priority.value,
        "story_points": str(ticket.story_points or ""),
        "due_date": ticket.due_date.isoformat() if ticket.due_date else "",
        "assignee_id": ticket.assignee_id or "",
        "status": ticket.status.value,
        "resolution_notes": ticket.resolution_notes or "",
        "blocked_reason": ticket.blocked_reason or "",
        "sprint_id": ticket.sprint_id or "",
    }
    if submitted_values is not None:
        form_values.update(submitted_values)
    return render(
        request,
        "ticket_detail.html" if page else "partials/ticket_detail.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            "ticket_reference": reference,
            "branch_command": command,
            "development_rows": development,
            **subtask_context,
            "members": members,
            "comments": comments,
            "comment_error": comment_error,
            **_attachment_context(session, project, ticket, user),
            "sprints": sprints,
            "ticket_types": TicketType,
            "priorities": Priority,
            "columns": COLUMNS,
            "error": error,
            "description_editing": error is not None,
            "saved": saved,
            "form_values": form_values,
            "active_tab": "board",
            "selected_sprint_id": ticket.sprint_id,
            "detail_drawer": not page,
        },
        status_code=status_code,
        session=session if page else None,
    )


@router.get("/projects/{slug}/tickets/{ticket_number}")
def ticket_detail(
    slug: str,
    ticket_number: int,
    request: Request,
    card: bool = False,
    saved: bool = False,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    if card:
        assignee_names = {}
        if ticket.assignee_id:
            assignee = session.get(User, ticket.assignee_id)
            if assignee:
                assignee_names[assignee.id] = assignee.name or assignee.email
        return render(
            request,
            "partials/ticket_card.html",
            {
                "user": user,
                "project": project,
                "ticket": ticket,
                "assignee_names": assignee_names,
                "today": date.today(),
            },
        )
    return _ticket_detail(
        request,
        session,
        user,
        project,
        ticket,
        page=not request.headers.get("HX-Request"),
        saved=saved,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/comments",
    dependencies=[Depends(verify_csrf)],
)
def create_ticket_comment_form(
    slug: str,
    ticket_number: int,
    request: Request,
    tasks: BackgroundTasks,
    body: str = Form(""),
    mention_ids: list[str] | None = _MENTION_IDS_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    try:
        comments.add_comment(session, project, ticket, user, body, mention_ids or [], tasks)
    except HTTPException as exc:
        if request.headers.get("HX-Request") != "true":
            return _ticket_detail(
                request,
                session,
                user,
                project,
                ticket,
                page=True,
                comment_error=exc.detail,
                status_code=exc.status_code,
            )
        return _render_ticket_comments(
            request,
            session,
            user,
            project,
            ticket,
            error=exc.detail,
            status_code=exc.status_code,
        )
    if request.headers.get("HX-Request") == "true":
        return _render_ticket_comments(request, session, user, project, ticket)
    return RedirectResponse(
        f"/projects/{project.slug}/tickets/{ticket.ticket_number}#ticket-comments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/comments/{comment_id}/edit",
    dependencies=[Depends(verify_csrf)],
)
def edit_ticket_comment_form(
    slug: str,
    ticket_number: int,
    comment_id: str,
    request: Request,
    tasks: BackgroundTasks,
    body: str = Form(""),
    mention_ids: list[str] | None = _MENTION_IDS_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    comment = comments.get_comment(session, ticket, comment_id)
    try:
        comments.edit_comment(
            session, project, ticket, comment, user, body, mention_ids or [], tasks
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise
        if request.headers.get("HX-Request") != "true":
            return _ticket_detail(
                request,
                session,
                user,
                project,
                ticket,
                page=True,
                comment_error=exc.detail,
                status_code=exc.status_code,
            )
        return _render_ticket_comments(
            request,
            session,
            user,
            project,
            ticket,
            error=exc.detail,
            status_code=exc.status_code,
        )
    if request.headers.get("HX-Request") == "true":
        return _render_ticket_comments(request, session, user, project, ticket)
    return RedirectResponse(
        f"/projects/{project.slug}/tickets/{ticket.ticket_number}#ticket-comments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/comments/{comment_id}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_ticket_comment_form(
    slug: str,
    ticket_number: int,
    comment_id: str,
    request: Request,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, membership = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    comment = comments.get_comment(session, ticket, comment_id)
    comments.delete_comment(session, comment, user, membership.role)
    if request.headers.get("HX-Request") == "true":
        return _render_ticket_comments(request, session, user, project, ticket)
    return RedirectResponse(
        f"/projects/{project.slug}/tickets/{ticket.ticket_number}#ticket-comments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/attachments",
    dependencies=[Depends(verify_csrf)],
)
def upload_ticket_attachments(
    slug: str,
    ticket_number: int,
    request: Request,
    files: list[UploadFile] = _FILES_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    uploads = (
        (
            upload.filename,
            upload.content_type,
            upload.file.read(attachments.ATTACHMENT_MAX_BYTES + 1),
        )
        for upload in files
    )
    try:
        attachments.attach_uploads(session, ticket, user.id, uploads)
    except HTTPException as exc:
        return _render_ticket_attachments(
            request, session, user, project, ticket, error=exc.detail, status_code=exc.status_code
        )
    return _render_ticket_attachments(request, session, user, project, ticket)


@router.get("/projects/{slug}/attachments/{attachment_id}")
def ticket_attachment_file(
    slug: str,
    attachment_id: str,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_reader),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    row = session.get(TicketAttachment, attachment_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return attachments.file_response(row)


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/attachments/{attachment_id}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_ticket_attachment(
    slug: str,
    ticket_number: int,
    attachment_id: str,
    request: Request,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, membership = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    attachments.delete_attachment(session, ticket, attachment_id, user, membership.role)
    return _render_ticket_attachments(request, session, user, project, ticket)


@router.post("/projects/{slug}/tickets/{ticket_number}", dependencies=[Depends(verify_csrf)])
def update_ticket_form(
    slug: str,
    ticket_number: int,
    request: Request,
    tasks: BackgroundTasks,
    title: str = Form(""),
    description: str = Form(""),
    type: str = Form(""),
    priority: str = Form(""),
    story_points: str = Form(""),
    due_date: date | None = _DUE_DATE_FORM,
    assignee_id: str = Form(""),
    status_value: str = Form("", alias="status"),
    resolution_notes: str = Form(""),
    blocked_reason: str = Form(""),
    sprint_id: str = Form(""),
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    is_hx = bool(request.headers.get("HX-Request"))
    submitted_values = {
        "title": title,
        "description": description,
        "type": type,
        "priority": priority,
        "story_points": story_points,
        "due_date": due_date.isoformat() if due_date else "",
        "assignee_id": assignee_id,
        "status": status_value,
        "resolution_notes": resolution_notes,
        "blocked_reason": blocked_reason,
        "sprint_id": sprint_id,
    }
    try:
        points = int(story_points) if story_points else None
        changes = TicketUpdate(
            title=title,
            description=description,
            type=type,
            priority=priority,
            story_points=points,
            due_date=due_date,
            assignee_id=assignee_id or None,
            resolution_notes=resolution_notes or None,
            blocked_reason=blocked_reason or None,
            sprint_id=sprint_id or None,
        ).model_dump(exclude_unset=True)
        status_update = StatusUpdate(status=status_value, resolution_notes=resolution_notes or None)
        changed_fields = [
            field for field, value in changes.items() if getattr(ticket, field) != value
        ]
        if ticket.status is not status_update.status:
            changed_fields.append("status")
        if changed_fields:
            meta = dict(ticket.meta)
            activity = list(meta.get("activity", []))
            activity.append(
                {
                    "at": utcnow().isoformat(),
                    "actor": user.name,
                    "summary": f"Updated: {', '.join(changed_fields)}",
                }
            )
            meta["activity"] = activity[-50:]
            changes["meta"] = meta
        update_ticket(session, ticket, project, commit=False, **changes)
        ticket = set_status(
            session,
            ticket,
            status_update.status,
            status_update.resolution_notes,
            project=project,
            tasks=tasks,
        )
    except (ValidationError, ValueError) as exc:
        error = (
            exc.errors()[0]["msg"]
            if isinstance(exc, ValidationError)
            else "points must be a number"
        )
        return _ticket_detail(
            request,
            session,
            user,
            project,
            ticket,
            error=error,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            page=not is_hx,
            submitted_values=submitted_values,
        )
    except HTTPException as exc:
        return _ticket_detail(
            request,
            session,
            user,
            project,
            ticket,
            error=exc.detail,
            status_code=exc.status_code,
            page=not is_hx,
            submitted_values=submitted_values,
        )
    if not is_hx:
        return RedirectResponse(
            f"/projects/{project.slug}/tickets/{ticket.ticket_number}?saved=true",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    response = _ticket_detail(request, session, user, project, ticket, saved=True)
    current_url = urlparse(request.headers.get("HX-Current-URL", ""))
    current_filters = parse_qs(current_url.query)
    location_changed = "status" in changed_fields or "sprint_id" in changed_fields
    filter_fields = {
        "mine": "assignee_id",
        "assignee_id": "assignee_id",
        "type": "type",
        "priority": "priority",
    }
    filter_changed = any(
        field in changed_fields
        for name, field in filter_fields.items()
        if name in current_filters
    )
    page_refresh_needed = (
        location_changed
        or current_url.path.endswith("/backlog")
        or filter_changed
    )
    if page_refresh_needed:
        response.headers["HX-Refresh"] = "true"
    else:
        events = {f"refresh-ticket-card-{ticket.id}": {}}
        if ticket.creator_id == user.id:
            events[f"refresh-my-request-{ticket.id}"] = {"title": ticket.title}
        response.headers["HX-Trigger"] = (
            json.dumps(events) if len(events) > 1 else next(iter(events))
        )
    return response


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_ticket_form(
    request: Request,
    ticket_number: int,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    ticket = _project_ticket(session, project, ticket_number)
    try:
        delete_ticket_record(session, ticket)
    except HTTPException as exc:
        return Response(str(exc.detail), status_code=exc.status_code, media_type="text/plain")
    if request.headers.get("HX-Request"):
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers["HX-Refresh"] = "true"
        return response
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{slug}/tickets", dependencies=[Depends(verify_csrf)])
def create_ticket_form(
    request: Request,
    tasks: BackgroundTasks,
    title: str = Form(...),
    type: TicketType = _DEFAULT_TICKET_TYPE,
    description: str = Form(""),
    sprint_id: str | None = Form(None),
    due_date: date | None = _DUE_DATE_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    # create_ticket (app/services.py) is the same function the JSON route calls -- atomic
    # ticket-number allocation, title/description/story_points/meta limits (Ruling R24, since
    # this form bypasses any Pydantic schema), and the TICKET_CREATED notification all live
    # there exactly once.
    try:
        ticket = create_ticket(
            session,
            project,
            user,
            title=title,
            description=description,
            type=type,
            priority=Priority.MEDIUM,
            due_date=due_date,
            sprint_id=sprint_id,
            tasks=tasks,
        )
    except HTTPException as exc:
        # Not a page and not JSON: the modal's error slot renders whatever text comes back
        # (see partials/ticket_modal.html's htmx:after-request handler), so plain text is enough.
        return Response(str(exc.detail), status_code=exc.status_code, media_type="text/plain")
    if request.headers.get("HX-Request"):
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers["HX-Redirect"] = f"/projects/{project.slug}"
        return response
    return RedirectResponse(f"/projects/{project.slug}", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/subtasks",
    dependencies=[Depends(verify_csrf)],
)
def create_subtask_form(
    slug: str,
    ticket_number: int,
    request: Request,
    title: str = Form("", alias="subtask_title"),
    description: str = Form("", alias="subtask_description"),
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    try:
        create_subtask(session, ticket, title=title, description=description)
    except HTTPException as exc:
        return _ticket_detail(
            request,
            session,
            user,
            project,
            ticket,
            error=exc.detail,
            status_code=exc.status_code,
        )
    return render(
        request,
        "partials/ticket_subtasks.html",
        {
            "user": user,
            "ticket": ticket,
            "project": project,
            **_subtask_context(session, ticket),
            "subtask_error": None,
        },
    )


@router.patch(
    "/projects/{slug}/tickets/{ticket_number}/subtasks/{subtask_id}",
    dependencies=[Depends(verify_csrf)],
)
def update_subtask_status(
    slug: str,
    ticket_number: int,
    subtask_id: str,
    request: Request,
    title: str | None = Form(None, alias="subtask_title"),
    description: str | None = Form(None, alias="subtask_description"),
    status_value: SubtaskStatus | None = Form(None, alias="subtask_status"),
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    subtask = session.get(Subtask, subtask_id)
    if subtask is None or subtask.ticket_id != ticket.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subtask not found")
    if title is None and description is None and status_value is None:
        status_value = SubtaskStatus.TODO
    update_subtask(
        session,
        subtask,
        title=title,
        description=description,
        status_value=status_value,
    )
    return render(
        request,
        "partials/ticket_subtasks.html",
        {
            "user": user,
            "ticket": ticket,
            "project": project,
            **_subtask_context(session, ticket),
            "subtask_error": None,
        },
    )


@router.delete(
    "/projects/{slug}/tickets/{ticket_number}/subtasks/{subtask_id}",
    dependencies=[Depends(verify_csrf)],
)
def delete_subtask_form(
    slug: str,
    ticket_number: int,
    subtask_id: str,
    request: Request,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    delete_subtask_record(session, ticket, subtask_id)
    return render(
        request,
        "partials/ticket_subtasks.html",
        {
            "user": user,
            "ticket": ticket,
            "project": project,
            **_subtask_context(session, ticket),
            "subtask_error": None,
        },
    )


@router.post("/projects/{slug}/tickets/{ticket_number}/status", dependencies=[Depends(verify_csrf)])
def change_status_form(
    request: Request,
    ticket_number: int,
    tasks: BackgroundTasks,
    status_value: TicketStatus = _STATUS_FORM_FIELD,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    # Board renders human-readable ticket_number, not id -- the one place in the
    # product a ticket is addressed this way. Numbers are project-scoped, so the
    # lookup filters on project_id *and* ticket_number together: number alone
    # would resolve across projects.
    ticket = _project_ticket(session, project, ticket_number)
    # set_status (app/services.py) owns the transition rules -- completed_at,
    # resolution_notes preservation, idempotency, and the TICKET_DONE
    # notification -- shared with the JSON route (app/routers/api_tickets.py).
    ticket = set_status(session, ticket, status_value, project=project, tasks=tasks)
    assignee_names = {}
    if ticket.assignee_id:
        assignee = session.get(User, ticket.assignee_id)
        if assignee:
            assignee_names[assignee.id] = assignee.name or assignee.email
    # Ruling R41: the card needs csrf_token to render its status control with a
    # working token, or the *next* status change on this page 403s.
    return render(
        request,
        "partials/ticket_card.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            "columns": COLUMNS,
            "assignee_names": assignee_names,
            "today": date.today(),
        },
    )
