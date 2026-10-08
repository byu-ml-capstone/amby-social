import asyncio
import hashlib
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlmodel import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.auth import SESSION_COOKIE
from app.config import settings
from app.db import get_session
from app.gmail import gmail_is_configured
from app.gmail_sync import poll_forever
from app.mcp_server import MCPHTTPApp, http_session_manager
from app.rendering import render_markdown
from app.routers import (
    api_auth,
    api_projects,
    api_sprints,
    api_tickets,
    api_tokens,
    github_webhook,
    gmail,
    web,
    web_sprints,
)
from app.security import enforce_auth_rate_limit


@asynccontextmanager
async def lifespan(_app: FastAPI):
    poller = asyncio.create_task(poll_forever()) if gmail_is_configured() else None
    try:
        async with http_session_manager(settings.allowed_hosts, settings.allowed_origins):
            yield
    finally:
        if poller is not None:
            poller.cancel()
            with suppress(asyncio.CancelledError):
                await poller


app = FastAPI(title="Kanban Flow", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(api_auth.router)
app.include_router(api_tokens.router)
app.include_router(api_projects.router)
app.include_router(api_sprints.router)
app.include_router(api_tickets.router)
app.include_router(github_webhook.router)
app.add_route("/mcp", MCPHTTPApp(), methods=["GET", "POST", "DELETE"])

UNSAFE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
AUTH_ACTIONS = {
    "/login": "login",
    "/register": "register",
    "/api/v1/auth/login": "login",
    "/api/v1/auth/register": "register",
}


def _set_security_headers(response: Response) -> Response:
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    return _set_security_headers(await call_next(request))


@app.middleware("http")
async def limit_auth_requests(request: Request, call_next):
    action = AUTH_ACTIONS.get(request.url.path)
    if request.method == "POST" and action:
        try:
            enforce_auth_rate_limit(request, action)
        except HTTPException as exc:
            return _set_security_headers(
                JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
                )
            )
    return await call_next(request)


@app.middleware("http")
async def block_foreign_origin_cookie_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method in UNSAFE_METHODS
        and request.url.path.startswith("/api/v1/")
        and SESSION_COOKIE in request.cookies
        and "authorization" not in request.headers
        and origin
        and origin not in settings.allowed_origins
    ):
        return _set_security_headers(
            JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        )
    return await call_next(request)


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["markdown"] = render_markdown
# Content hash per file, so a deploy changes the URL and browsers drop their cached copy.
templates.env.globals["asset_version"] = {
    name: hashlib.sha256((Path(__file__).parent / "static" / name).read_bytes()).hexdigest()[:10]
    for name in ("app.js", "app.css")
}

app.include_router(web.router)
# Before web_sprints: its /settings/integrations/{provider}/disconnect would swallow gmail.
app.include_router(gmail.router)
app.include_router(web_sprints.router)


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    # The container healthcheck hits this, so it must fail when the database does.
    session.execute(text("SELECT 1"))
    return {"status": "ok"}
