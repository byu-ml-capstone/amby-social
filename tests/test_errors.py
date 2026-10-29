from fastapi import HTTPException

from app.errors import BadRequest, Conflict, DomainError, Forbidden, Invalid, NotFound, TooLarge


def test_domain_errors_carry_their_http_status_and_detail():
    statuses = {BadRequest: 400, Forbidden: 403, NotFound: 404, Conflict: 409, TooLarge: 413}
    for error_type, code in {**statuses, Invalid: 422}.items():
        error = error_type("why")
        assert isinstance(error, DomainError) and isinstance(error, HTTPException)
        assert (error.status_code, error.detail) == (code, "why")
