import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import Session

from app.config import settings
from app.db import get_engine
from app.github import GitHubClient
from app.github_sync import process_github_delivery

router = APIRouter(tags=["github"])


def valid_webhook_signature(body: bytes, header: str | None, secret: str) -> bool:
    if not isinstance(header, str):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(header, expected)
    except TypeError:
        return False


@router.post("/integrations/github/webhook")
async def github_webhook(request: Request) -> Response:
    raw = await request.body()
    secret = settings.github_webhook_secret
    if not secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GitHub webhook is not configured",
        )
    if not valid_webhook_signature(
        raw,
        request.headers.get("X-Hub-Signature-256"),
        secret,
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid GitHub webhook signature",
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid GitHub webhook JSON",
        ) from None
    delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
    event_type = request.headers.get("X-GitHub-Event", "").strip()
    if not delivery_id or not event_type or not isinstance(payload, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid GitHub webhook metadata",
        )
    with Session(get_engine()) as session, GitHubClient() as github:
        process_github_delivery(session, delivery_id, event_type, payload, github)
    return Response(status_code=status.HTTP_200_OK)
