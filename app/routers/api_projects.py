from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session, select

from app.auth import current_user, project_owner, project_reader
from app.db import get_session
from app.models import (
    Project,
    ProjectMember,
    Role,
    User,
)
from app.schemas import MemberAdd, MemberOut, MemberUpdate, ProjectCreate, ProjectOut, ProjectUpdate

# slugify moved to app.services (single source of truth, shared with the
# dashboard form route) but stays importable from here: tests/conftest.py and
# tests/test_projects.py import it from this module.
from app.services import (  # noqa: F401
    add_project_member,
    create_project,
    project_members,
    remove_project_member,
    slugify,
    update_project_member,
)
from app.services import delete_project as delete_project_service
from app.services import update_project as update_project_service

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _out(project: Project, role: Role | None) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        slug=project.slug,
        key=project.key,
        created_at=project.created_at,
        role=role.value if role else None,
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def post_project(
    body: ProjectCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    project = create_project(session, body.name, user)
    return _out(project, Role.OWNER)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    user: User = Depends(current_user), session: Session = Depends(get_session)
) -> list[ProjectOut]:
    rows = session.exec(
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at.desc())
    ).all()
    return [_out(project, member.role) for project, member in rows]


@router.get("/{slug}", response_model=ProjectOut)
def get_project(access=Depends(project_reader)):
    project, member = access
    return _out(project, member.role)


@router.patch("/{slug}", response_model=ProjectOut)
def update_project(
    body: ProjectUpdate,
    access=Depends(project_owner),
    session: Session = Depends(get_session),
):
    project, member = access
    update_project_service(
        session, project, **body.model_dump(exclude_unset=True, exclude_none=True)
    )
    return _out(project, member.role)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    confirm: str = Query(default=""),
    access=Depends(project_owner),
    session: Session = Depends(get_session),
) -> None:
    project, _ = access
    delete_project_service(session, project, confirm)


def _members(session: Session, project_id: str) -> list[MemberOut]:
    rows = project_members(session, project_id)
    return [
        MemberOut(
            user_id=user.id,
            name=user.name,
            email=user.email,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, user in rows
    ]


@router.get("/{slug}/members", response_model=list[MemberOut])
def list_members(access=Depends(project_reader), session: Session = Depends(get_session)):
    project, _ = access
    return _members(session, project.id)


@router.post("/{slug}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    body: MemberAdd, access=Depends(project_owner), session: Session = Depends(get_session)
):
    project, _ = access
    member = add_project_member(session, project, str(body.email), body.role)
    return next(m for m in _members(session, project.id) if m.user_id == member.user_id)


@router.patch("/{slug}/members/{user_id}", response_model=MemberOut)
def update_member(
    user_id: str,
    body: MemberUpdate,
    access=Depends(project_owner),
    session: Session = Depends(get_session),
):
    project, _ = access
    update_project_member(session, project, user_id, body.role)
    return next(m for m in _members(session, project.id) if m.user_id == user_id)


@router.delete("/{slug}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: str, access=Depends(project_owner), session: Session = Depends(get_session)
) -> None:
    project, _ = access
    remove_project_member(session, project, user_id)
