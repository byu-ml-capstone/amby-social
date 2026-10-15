"""Rendering helpers shared by the web routers."""

from fastapi import Request, Response
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Project, ProjectMember, Sprint, SprintStatus, TicketStatus, User

COLUMNS = (
    TicketStatus.BACKLOG,
    TicketStatus.SELECTED,
    TicketStatus.IN_PROGRESS,
    TicketStatus.DONE,
)


def render(
    request: Request,
    name: str,
    context: dict,
    status_code: int = 200,
    session: Session | None = None,
) -> Response:
    from app.main import templates

    user = context.get("user")
    shell_context = {}
    if session and user and not name.startswith("partials/"):
        shell_context = _shell_context(session, user)
        project = context.get("project")
        if project is not None:
            status_order = {
                SprintStatus.ACTIVE: 0,
                SprintStatus.PLANNING: 1,
                SprintStatus.CLOSED: 2,
            }
            shell_context["sprint_options"] = sorted(
                session.exec(select(Sprint).where(Sprint.project_id == project.id)).all(),
                key=lambda sprint: (status_order[sprint.status], sprint.start_date),
            )
            shell_context["current_sprint"] = next(
                (
                    sprint
                    for sprint in shell_context["sprint_options"]
                    if sprint.status in (SprintStatus.ACTIVE, SprintStatus.PLANNING)
                ),
                None,
            )
    context = {
        **shell_context,
        "request": request,
        "csrf_token": make_csrf_token(user.id) if user else "",
        **context,
    }
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _shell_context(session: Session, user: User) -> dict:
    rows = session.exec(
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at.desc())
    ).all()
    return {
        "projects": [project for project, _ in rows],
        "roles": {project.id: member.role.value for project, member in rows},
    }
