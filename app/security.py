import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request, status

_LIMITS = {"login": (5, 60), "register": (3, 3600)}
_auth_attempts: dict[tuple[str, str], deque[float]] = {}
_auth_attempts_lock = Lock()


def enforce_auth_rate_limit(request: Request, action: str) -> None:
    limit, window = _LIMITS[action]
    client = request.client.host if request.client else "unknown"
    key = (action, client)
    now = time.monotonic()

    with _auth_attempts_lock:
        attempts = _auth_attempts.setdefault(key, deque())
        while attempts and attempts[0] <= now - window:
            attempts.popleft()
        if len(attempts) >= limit:
            retry_after = max(1, int(window - (now - attempts[0])))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many authentication attempts",
                headers={"Retry-After": str(retry_after)},
            )
        attempts.append(now)

        # ponytail: one-process bounded cache; move rate limiting to Redis/proxy
        # when the application runs multiple workers or instances.
        if len(_auth_attempts) > 10_000:
            _auth_attempts.pop(next(iter(_auth_attempts)))
