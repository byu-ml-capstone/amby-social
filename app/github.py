import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urljoin, urlsplit

import httpx
import jwt
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, select

from app.config import Settings, settings
from app.models import GitHubConnectState, Project, ProjectMember, Role, utcnow

_STATE_TTL = timedelta(minutes=10)
_STATE_SERIALIZER = URLSafeTimedSerializer(settings.session_secret, salt="kf-github-connect")
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass(frozen=True)
class AvailableRepository:
    id: int
    full_name: str
    html_url: str
    default_branch: str


def github_is_configured(config: Settings = settings) -> bool:
    return all(
        (
            config.github_app_id,
            config.github_app_slug,
            config.github_client_id,
            config.github_client_secret,
            config.github_private_key,
            config.github_webhook_secret,
        )
    )


def issue_github_state(
    session: Session,
    project_id: str,
    user_id: str,
    pending_installation_id: int | None = None,
    now: datetime | None = None,
) -> str:
    now = now or utcnow()
    nonce = secrets.token_urlsafe(32)
    session.add(
        GitHubConnectState(
            id=hashlib.sha256(nonce.encode()).hexdigest(),
            project_id=project_id,
            user_id=user_id,
            pending_installation_id=pending_installation_id,
            expires_at=now + _STATE_TTL,
            created_at=now,
        )
    )
    session.commit()
    return _STATE_SERIALIZER.dumps({"nonce": nonce})


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _validated_github_state(
    session: Session,
    signed_state: str,
    user_id: str,
    now: datetime | None = None,
) -> GitHubConnectState:
    try:
        payload = _STATE_SERIALIZER.loads(signed_state)
        nonce = payload["nonce"]
        if not isinstance(nonce, str) or not nonce:
            raise ValueError
    except (BadData, KeyError, TypeError, ValueError):
        raise HTTPException(400, "Invalid GitHub state") from None

    row = session.get(GitHubConnectState, hashlib.sha256(nonce.encode()).hexdigest())
    if row is None:
        raise HTTPException(400, "Invalid GitHub state")
    if row.user_id != user_id:
        raise HTTPException(403, "GitHub state belongs to another user")
    if row.consumed_at is not None:
        raise HTTPException(400, "GitHub state already used")
    if _as_utc(row.expires_at) <= _as_utc(now or utcnow()):
        raise HTTPException(400, "GitHub state expired")

    project = session.get(Project, row.project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    membership = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == row.project_id,
            ProjectMember.user_id == user_id,
        )
    ).first()
    if membership is None or membership.role != Role.OWNER:
        raise HTTPException(403, "Project owner role required")
    return row


def read_github_state(
    session: Session,
    signed_state: str,
    user_id: str,
    now: datetime | None = None,
) -> GitHubConnectState:
    return _validated_github_state(session, signed_state, user_id, now)


def consume_github_state(
    session: Session,
    signed_state: str,
    user_id: str,
    now: datetime | None = None,
) -> GitHubConnectState:
    now = now or utcnow()
    row = _validated_github_state(session, signed_state, user_id, now)
    claim = session.exec(
        update(GitHubConnectState)
        .where(
            GitHubConnectState.id == row.id,
            GitHubConnectState.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    if claim.rowcount != 1:
        session.rollback()
        raise HTTPException(400, "GitHub state already used")
    session.commit()
    claimed = session.get(GitHubConnectState, row.id)
    if claimed is None:  # pragma: no cover - the row was updated in the same transaction
        raise HTTPException(400, "Invalid GitHub state")
    set_committed_value(claimed, "consumed_at", _as_utc(claimed.consumed_at))
    return claimed


class GitHubClient:
    def __init__(
        self,
        config: Settings = settings,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.Client()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _api_headers(self, token: str) -> dict[str, str]:
        return {**_API_HEADERS, "Authorization": f"Bearer {token}"}

    def _app_token(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iat": now - 60,
                "exp": now + 540,
                "iss": self._config.github_app_id,
            },
            self._config.github_private_key,
            algorithm="RS256",
        )

    def _api_url(self, path: str) -> str:
        return urljoin(f"{self._config.github_api_url.rstrip('/')}/", path)

    def exchange_code(self, code: str) -> str:
        response = self._client.post(
            urljoin(self._config.github_web_url, "/login/oauth/access_token"),
            headers={"Accept": "application/json"},
            data={
                "client_id": self._config.github_client_id,
                "client_secret": self._config.github_client_secret,
                "code": code,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            raise ValueError("GitHub OAuth returned invalid JSON") from None
        if not isinstance(body, dict):
            raise ValueError("GitHub OAuth returned invalid JSON")
        if body.get("error"):
            raise ValueError(f"GitHub OAuth error: {body['error']}")
        token = body.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("GitHub OAuth response missing access token")
        return token

    def verify_user_installation(self, user_token: str, installation_id: int) -> None:
        response = self._client.get(
            self._api_url(
                f"user/installations/{quote(str(installation_id), safe='')}/repositories"
            ),
            headers=self._api_headers(user_token),
            timeout=10.0,
        )
        response.raise_for_status()

    def installation(self, installation_id: int) -> dict:
        response = self._client.get(
            self._api_url(f"app/installations/{quote(str(installation_id), safe='')}"),
            headers=self._api_headers(self._app_token()),
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    def _installation_token(self, installation_id: int) -> str:
        response = self._client.post(
            self._api_url(
                f"app/installations/{quote(str(installation_id), safe='')}/access_tokens"
            ),
            headers=self._api_headers(self._app_token()),
            timeout=10.0,
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("GitHub installation token response missing token")
        return token

    def _paginated(
        self,
        path: str,
        headers: dict[str, str],
        params: dict[str, str] | None,
        item_key: str | None = None,
    ) -> list[dict]:
        items = []
        url: str | None = path
        query = params
        expected = urlsplit(self._config.github_api_url)
        while url:
            absolute = self._api_url(url)
            actual = urlsplit(absolute)
            if (actual.scheme, actual.hostname, actual.port) != (
                expected.scheme,
                expected.hostname,
                expected.port,
            ):
                raise ValueError("GitHub pagination left configured API origin")
            response = self._client.get(
                absolute,
                headers=headers,
                params=query,
                timeout=10.0,
            )
            response.raise_for_status()
            body = response.json()
            items.extend(body[item_key] if item_key else body)
            url = response.links.get("next", {}).get("url")
            query = None
        return items

    @staticmethod
    def _repository_path(full_name: str) -> str:
        owner, repository = full_name.split("/", 1)
        return f"{quote(owner, safe='')}/{quote(repository, safe='')}"

    def repositories(self, installation_id: int) -> list[AvailableRepository]:
        token = self._installation_token(installation_id)
        repositories = self._paginated(
            "installation/repositories",
            self._api_headers(token),
            {"per_page": "100"},
            "repositories",
        )
        return [
            AvailableRepository(
                repository["id"],
                repository["full_name"],
                repository["html_url"],
                repository["default_branch"],
            )
            for repository in repositories
        ]

    def open_pull_requests(self, installation_id: int, full_name: str) -> list[dict]:
        token = self._installation_token(installation_id)
        return self._paginated(
            f"repos/{self._repository_path(full_name)}/pulls",
            self._api_headers(token),
            {"state": "open", "per_page": "100"},
        )

    def pull_request_reviews(
        self,
        installation_id: int,
        full_name: str,
        number: int,
    ) -> list[dict]:
        token = self._installation_token(installation_id)
        return self._paginated(
            f"repos/{self._repository_path(full_name)}/pulls/{quote(str(number), safe='')}/reviews",
            self._api_headers(token),
            {"per_page": "100"},
        )

    def check_runs(self, installation_id: int, full_name: str, sha: str) -> list[dict]:
        token = self._installation_token(installation_id)
        return self._paginated(
            f"repos/{self._repository_path(full_name)}/commits/{quote(sha, safe='')}/check-runs",
            self._api_headers(token),
            {"per_page": "100"},
            "check_runs",
        )

    def pull_requests_for_commit(
        self,
        installation_id: int,
        full_name: str,
        sha: str,
    ) -> list[dict]:
        token = self._installation_token(installation_id)
        return self._paginated(
            f"repos/{self._repository_path(full_name)}/commits/{quote(sha, safe='')}/pulls",
            self._api_headers(token),
            {"per_page": "100"},
        )
