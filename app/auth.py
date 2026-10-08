import hashlib
import secrets
from datetime import timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import ApiToken, Project, ProjectMember, Role, User, utcnow

SESSION_COOKIE = "kf_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14
_SESSION_SALT = "kf-session"

_serializer = URLSafeTimedSerializer(settings.session_secret, salt=_SESSION_SALT)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    # bcrypt raises ValueError for a >72-byte plaintext; fail closed here rather
    # than raise, since login must run to completion even for bad input. Registration
    # enforces the 72-byte limit up front (Task 6's RegisterRequest), so don't add a
    # matching guard to hash_password — that would just duplicate the validation.
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


DUMMY_HASH = hash_password("kanbanflow-dummy-password-for-constant-time-login")


def make_session_cookie(user_id: str) -> str:
    return _serializer.dumps(user_id)


def read_session_cookie(raw: str) -> str | None:
    try:
        return _serializer.loads(raw, max_age=SESSION_MAX_AGE)
    except BadData:
        return None


def issue_api_token(
    session: Session, user: User, label: str, scope: str = "read"
) -> tuple[ApiToken, str]:
    plaintext = f"kf_{secrets.token_urlsafe(32)}"
    token = ApiToken(
        user_id=user.id,
        label=label,
        prefix=plaintext[:10],
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope=scope,
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    return token, plaintext


def _bearer_token(authorization: str) -> str | None:
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None
    return parts[1]


def _is_older_than_an_hour(value) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=utcnow().tzinfo)
    return utcnow() - value > timedelta(hours=1)


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    authorization = request.headers.get("authorization")
    if authorization is not None:
        plaintext = _bearer_token(authorization)
        if plaintext is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        token = session.exec(
            select(ApiToken).where(ApiToken.token_hash == token_hash, ApiToken.revoked_at.is_(None))
        ).first()
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        expires_at = token.expires_at
        if expires_at is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
        if expires_at <= utcnow():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and token.scope != "write":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "API token is read-only")
        user = session.get(User, token.user_id)
        if user is None or user.deleted_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        if _is_older_than_an_hour(token.last_used_at):
            token.last_used_at = utcnow()
            session.add(token)
            session.commit()
        return user

    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    user_id = read_session_cookie(raw)
    if user_id is None:
        return None
    user = session.get(User, user_id)
    return user if user is not None and user.deleted_at is None else None


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CSRF_FIELD = "_csrf"
CSRF_MAX_AGE = 60 * 60 * 24
_csrf_serializer = URLSafeTimedSerializer(settings.session_secret, salt="kf-csrf")


def make_csrf_token(user_id: str) -> str:
    return _csrf_serializer.dumps(user_id)


def read_csrf_token(raw: str) -> str | None:
    try:
        return _csrf_serializer.loads(raw, max_age=CSRF_MAX_AGE)
    except BadData:
        return None


def assert_csrf_matches(raw: str | None, user_id: str) -> None:
    if not raw or read_csrf_token(raw) != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")


async def verify_csrf(request: Request, user: User = Depends(current_user)) -> None:
    form = await request.form()
    assert_csrf_matches(form.get(CSRF_FIELD), user.id)


def load_project_and_membership(
    slug: str, user: User, session: Session
) -> tuple[Project, ProjectMember | None]:
    project = session.exec(select(Project).where(Project.slug == slug)).first()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
        )
    ).first()
    return project, member


def project_reader(
    slug: str, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> tuple[Project, ProjectMember]:
    project, member = load_project_and_membership(slug, user, session)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project, member


def require_member(member: ProjectMember | None) -> ProjectMember:
    """The one place a missing membership becomes 403 on a write path. Every
    write-side dependency or helper -- whether it starts from a slug
    (project_writer) or a ticket id (api_tickets.load_ticket_for_write) --
    routes through this instead of re-deciding the status itself."""
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")
    return member


def project_writer(
    slug: str, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> tuple[Project, ProjectMember]:
    project, member = load_project_and_membership(slug, user, session)
    return project, require_member(member)


def project_owner(
    slug: str, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> tuple[Project, ProjectMember]:
    project, member = project_writer(slug, user, session)
    if member.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
    return project, member
