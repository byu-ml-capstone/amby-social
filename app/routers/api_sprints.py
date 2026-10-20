from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.auth import current_user, project_owner, project_reader, require_member
from app.db import get_session
from app.models import (
    Project,
    ProjectMember,
    Role,
    Sprint,
    SprintReport,
    SprintTicketHistory,
    Ticket,
    User,
)
from app.schemas import (
    SprintClose,
    SprintCreate,
    SprintHistoryOut,
    SprintOut,
    SprintReportOut,
    SprintReportUpdate,
    SprintUpdate,
)
from app.services import (
    close_sprint,
    create_sprints,
    sprint_chronology,
    start_sprint,
    update_sprint,
)
from app.sprint_reports import build_payload, finalize_report, get_report, save_report

router = APIRouter(prefix="/api/v1", tags=["sprints"])


def _sprint_access(sprint_id: str, user: User, session: Session, *, owner: bool = False) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == sprint.project_id, ProjectMember.user_id == user.id
        )
    ).first()
    if not owner and member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")
    member = require_member(member)
    if owner and member.role is not Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
    return sprint


@router.get("/projects/{slug}/sprints", response_model=list[SprintOut])
def list_sprints(
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    session: Session = Depends(get_session),
) -> list[Sprint]:
    project, _ = access
    return session.exec(
        select(Sprint).where(Sprint.project_id == project.id).order_by(*sprint_chronology())
    ).all()


@router.post(
    "/projects/{slug}/sprints", response_model=list[SprintOut], status_code=status.HTTP_201_CREATED
)
def post_sprint(
    body: SprintCreate,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> list[Sprint]:
    project, _ = access
    return create_sprints(session, project, body.count)


@router.get("/sprints/{sprint_id}", response_model=SprintOut)
def get_sprint(
    sprint_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Sprint:
    return _sprint_access(sprint_id, user, session)


@router.patch("/sprints/{sprint_id}", response_model=SprintOut)
def patch_sprint(
    sprint_id: str,
    body: SprintUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Sprint:
    sprint = _sprint_access(sprint_id, user, session, owner=True)
    changes = body.model_dump(exclude_unset=True)
    if changes.pop("status", None):
        if changes:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "status may not be combined with sprint details",
            )
        return start_sprint(session, sprint)
    return update_sprint(session, sprint, **changes)


@router.post("/sprints/{sprint_id}/close", response_model=SprintOut)
def post_close_sprint(
    sprint_id: str,
    body: SprintClose,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Sprint:
    sprint = _sprint_access(sprint_id, user, session, owner=True)
    return close_sprint(session, sprint)


@router.get("/sprints/{sprint_id}/history", response_model=list[SprintHistoryOut])
def get_sprint_history(
    sprint_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[SprintHistoryOut]:
    _sprint_access(sprint_id, user, session)
    rows = session.exec(
        select(SprintTicketHistory, Ticket)
        .join(Ticket, Ticket.id == SprintTicketHistory.ticket_id)
        .where(SprintTicketHistory.sprint_id == sprint_id)
        .order_by(Ticket.ticket_number)
    ).all()
    return [
        SprintHistoryOut(
            ticket_id=history.ticket_id,
            ticket_number=ticket.ticket_number,
            title=ticket.title,
            status_at_close=history.status_at_close,
            story_points_at_close=history.story_points_at_close,
            was_completed=history.was_completed,
        )
        for history, ticket in rows
    ]


@router.get("/sprints/{sprint_id}/report", response_model=SprintReportOut)
def get_sprint_report(
    sprint_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> SprintReport:
    return get_report(session, _sprint_access(sprint_id, user, session))


@router.get("/sprints/{sprint_id}/report/payload")
def get_sprint_report_payload(
    sprint_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> dict:
    sprint = _sprint_access(sprint_id, user, session)
    get_report(session, sprint)  # 404 until the sprint has closed
    return build_payload(session, sprint)


@router.put("/sprints/{sprint_id}/report", response_model=SprintReportOut)
def put_sprint_report(
    sprint_id: str,
    body: SprintReportUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> SprintReport:
    sprint = _sprint_access(sprint_id, user, session)
    changes = body.model_dump(exclude_unset=True, exclude_none=True, exclude={"generated_by"})
    return save_report(session, sprint, get_report(session, sprint), changes, body.generated_by)


@router.post("/sprints/{sprint_id}/report/finalize", response_model=SprintReportOut)
def post_finalize_sprint_report(
    sprint_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> SprintReport:
    sprint = _sprint_access(sprint_id, user, session, owner=True)
    return finalize_report(session, get_report(session, sprint), user)
