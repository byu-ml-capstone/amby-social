import base64
from urllib.parse import quote, urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer

from app.config import Settings, settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me/"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

_STATE_MAX_AGE = 600
_STATE_SERIALIZER = URLSafeTimedSerializer(settings.session_secret, salt="kf-gmail-connect")


class GmailAuthError(Exception):
    """The stored refresh token no longer works (invalid_grant); the owner must reconnect."""


def gmail_is_configured(config: Settings = settings) -> bool:
    return all(
        (
            config.google_client_id,
            config.google_client_secret,
            config.google_redirect_uri,
            config.token_encryption_key,
        )
    )


def encrypt_token(token: str, config: Settings = settings) -> str:
    return Fernet(config.token_encryption_key).encrypt(token.encode()).decode()


def decrypt_token(value: str, config: Settings = settings) -> str:
    return Fernet(config.token_encryption_key).decrypt(value.encode()).decode()


def issue_gmail_state(project_id: str, user_id: str) -> str:
    return _STATE_SERIALIZER.dumps({"project_id": project_id, "user_id": user_id})


def read_gmail_state(signed_state: str, user_id: str) -> str:
    try:
        payload = _STATE_SERIALIZER.loads(signed_state, max_age=_STATE_MAX_AGE)
        project_id = payload["project_id"]
        state_user_id = payload["user_id"]
    except (BadData, KeyError, TypeError):
        raise HTTPException(400, "Invalid Gmail state") from None
    if state_user_id != user_id:
        raise HTTPException(403, "Gmail state belongs to another user")
    return project_id


def authorize_url(state: str, config: Settings = settings) -> str:
    query = urlencode(
        {
            "client_id": config.google_client_id,
            "redirect_uri": config.google_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def _oauth_error(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("error") if isinstance(body, dict) else None


class GmailClient:
    def __init__(self, config: Settings = settings, client: httpx.Client | None = None) -> None:
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

    def _token_request(self, data: dict[str, str]) -> dict:
        response = self._client.post(
            TOKEN_URL,
            data={
                **data,
                "client_id": self._config.google_client_id,
                "client_secret": self._config.google_client_secret,
            },
            timeout=10.0,
        )
        if response.status_code == 400 and _oauth_error(response) == "invalid_grant":
            raise GmailAuthError("Google refresh token is no longer valid")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise ValueError("Google OAuth response missing access token")
        return body

    def exchange_code(self, code: str) -> tuple[str, str]:
        body = self._token_request(
            {
                "code": code,
                "redirect_uri": self._config.google_redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        refresh_token = body.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ValueError("Google OAuth response missing refresh token")
        return body["access_token"], refresh_token

    def access_token(self, refresh_token: str) -> tuple[str, int]:
        body = self._token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})
        expires_in = body.get("expires_in")
        return body["access_token"], expires_in if isinstance(expires_in, int) else 3600

    def revoke(self, refresh_token: str) -> None:
        response = self._client.post(REVOKE_URL, data={"token": refresh_token}, timeout=10.0)
        response.raise_for_status()

    def _get(
        self, access_token: str, path: str, params: dict | None = None, *, timeout: float = 10.0
    ) -> dict:
        response = self._client.get(
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Gmail API returned invalid JSON")
        return body

    def _pages(
        self, access_token: str, path: str, params: dict, item_key: str
    ) -> tuple[list[dict], dict]:
        items: list[dict] = []
        query = params
        while True:
            body = self._get(access_token, path, query)
            items.extend(body.get(item_key) or [])
            page_token = body.get("nextPageToken")
            if not page_token:
                return items, body
            query = {**params, "pageToken": page_token}

    def profile(self, access_token: str) -> dict:
        return self._get(access_token, "profile")

    def labels(self, access_token: str) -> list[dict]:
        labels = self._get(access_token, "labels").get("labels") or []
        return [label for label in labels if label.get("type") == "user"]

    def history(self, access_token: str, start_history_id: str) -> tuple[list[dict], str]:
        records, last_page = self._pages(
            access_token,
            "history",
            {"startHistoryId": start_history_id, "historyTypes": ["messageAdded", "labelAdded"]},
            "history",
        )
        return records, str(last_page.get("historyId") or start_history_id)

    def recent_label_messages(self, access_token: str, label_id: str) -> list[dict]:
        messages, _ = self._pages(
            access_token, "messages", {"labelIds": label_id, "q": "newer_than:2d"}, "messages"
        )
        return messages

    def message(self, access_token: str, message_id: str) -> dict:
        return self._get(access_token, f"messages/{quote(message_id, safe='')}", {"format": "full"})

    def attachment(self, access_token: str, message_id: str, attachment_id: str) -> bytes:
        body = self._get(
            access_token,
            f"messages/{quote(message_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
            timeout=30.0,  # up to 10 MB of base64
        )
        data = body.get("data") or ""
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
