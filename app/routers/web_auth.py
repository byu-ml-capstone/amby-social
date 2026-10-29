"""Login, registration and account settings pages."""

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import Session, select

from app.auth import (
    DUMMY_HASH,
    SESSION_COOKIE,
    current_user,
    issue_api_token,
    optional_user,
    verify_csrf,
    verify_password,
)
from app.db import get_session
from app.models import (
    ApiToken,
    User,
)
from app.routers.api_auth import _set_session
from app.routers.web_common import render
from app.services import (
    change_user_password,
    delete_account,
    register_user,
    revoke_api_token,
    update_user_profile,
)

router = APIRouter(tags=["web"])


# Reused to give the register form the same EmailStr format check RegisterRequest gives the
# JSON route, without hand-rolling a regex.
_email_adapter = TypeAdapter(EmailStr)


@router.get("/")
def index(user: User | None = Depends(optional_user)) -> Response:
    target = "/dashboard" if user else "/login"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(optional_user)) -> Response:
    if user:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", {"user": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    user = session.exec(
        select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
    ).first()
    # Mirror api_auth.login exactly: run verify_password on a real hash even for an unknown
    # email, so bcrypt's cost lands on both branches. Short-circuiting on `user is None` (as
    # the earlier version of this route did) kept the message identical but not the timing --
    # a non-existent account would return in microseconds while a wrong password cost a full
    # bcrypt round, exactly the gap DUMMY_HASH exists to close.
    hashed = user.password_hash if user else DUMMY_HASH
    if not verify_password(password, hashed) or user is None:
        return render(
            request,
            "login.html",
            {"user": None, "error": "Invalid email or password"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user.id)
    return response


@router.get("/register")
def register_page(request: Request, user: User | None = Depends(optional_user)) -> Response:
    if user:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "register.html", {"user": None})


@router.post("/register")
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    try:
        email = _email_adapter.validate_python(email)
    except ValidationError:
        return render(
            request,
            "register.html",
            {"user": None, "error": "Enter a valid email address"},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        user = register_user(session, name=name, email=str(email), password=password)
    except HTTPException as exc:
        error = (
            "That email is already registered"
            if exc.status_code == status.HTTP_409_CONFLICT
            else exc.detail
        )
        return render(
            request,
            "register.html",
            {"user": None, "error": error},
            status_code=exc.status_code,
        )
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user.id)
    return response


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout_submit() -> Response:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def _user_settings(
    request: Request,
    session: Session,
    user: User,
    *,
    error: str | None = None,
    status_code: int = 200,
    issued_token: str | None = None,
    profile_saved: bool = False,
    password_saved: bool = False,
) -> Response:
    tokens = session.exec(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
    ).all()
    return render(
        request,
        "user_settings.html",
        {
            "user": user,
            "tokens": tokens,
            "error": error,
            "issued_token": issued_token,
            "base_url": str(request.base_url).rstrip("/"),
            "profile_saved": profile_saved,
            "password_saved": password_saved,
            "account_settings": True,
        },
        status_code=status_code,
        session=session,
    )


def _settings_error(request: Request, session: Session, user: User, exc: HTTPException) -> Response:
    session.rollback()  # release the write lock a refused delete_account may hold
    return _user_settings(request, session, user, error=exc.detail, status_code=exc.status_code)


@router.get("/settings")
def user_settings(
    request: Request,
    profile_saved: bool = False,
    password_saved: bool = False,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    return _user_settings(
        request,
        session,
        user,
        profile_saved=profile_saved,
        password_saved=password_saved,
    )


@router.post("/settings/profile", dependencies=[Depends(verify_csrf)])
def update_profile(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    try:
        normalized_email = str(_email_adapter.validate_python(email))
    except ValidationError:
        return _user_settings(
            request,
            session,
            user,
            error="Enter a valid email address",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        update_user_profile(session, user, name=name, email=normalized_email)
    except HTTPException as exc:
        return _settings_error(request, session, user, exc)
    return RedirectResponse("/settings?profile_saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/password", dependencies=[Depends(verify_csrf)])
def update_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    if new_password != confirm_password:
        return _user_settings(
            request,
            session,
            user,
            error="New passwords do not match",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        change_user_password(
            session, user, current_password=current_password, new_password=new_password
        )
    except HTTPException as exc:
        return _settings_error(request, session, user, exc)
    return RedirectResponse("/settings?password_saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/tokens", dependencies=[Depends(verify_csrf)])
def create_user_token(
    request: Request,
    label: str = Form(""),
    scope: str = Form("read"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    label = label.strip()
    if not label or len(label) > 100:
        return _user_settings(
            request,
            session,
            user,
            error="Token label must be between 1 and 100 characters",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if scope not in {"read", "write"}:
        return _user_settings(
            request,
            session,
            user,
            error="Token scope must be read or write",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    _, plaintext = issue_api_token(session, user, label, scope)
    return _user_settings(
        request,
        session,
        user,
        issued_token=plaintext,
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/settings/tokens/{token_id}/revoke", dependencies=[Depends(verify_csrf)])
def revoke_user_token(
    token_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    revoke_api_token(session, user, token_id)
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/delete", dependencies=[Depends(verify_csrf)])
def delete_user_account(
    request: Request,
    current_password: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    try:
        delete_account(session, user, current_password=current_password)
    except HTTPException as exc:
        return _settings_error(request, session, user, exc)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
