"""Domain errors, each mapped to the HTTP status a route should answer with.

They subclass HTTPException so FastAPI and the routes' existing `except HTTPException` handling
keep working unchanged; domain modules raise these and leave status codes to this file.
"""

from fastapi import HTTPException, status


class DomainError(HTTPException):
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: str) -> None:
        super().__init__(type(self).status_code, detail)


class BadRequest(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST


class Forbidden(DomainError):
    status_code = status.HTTP_403_FORBIDDEN


class NotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND


class Conflict(DomainError):
    status_code = status.HTTP_409_CONFLICT


class TooLarge(DomainError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE


class Invalid(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
