from fastapi import APIRouter, Depends, status
from sqlmodel import Session, select

from app.auth import current_user, issue_api_token
from app.db import get_session
from app.models import ApiToken, User
from app.schemas import TokenCreate, TokenIssued, TokenOut
from app.services import revoke_api_token

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


@router.get("", response_model=list[TokenOut])
def list_tokens(
    user: User = Depends(current_user), session: Session = Depends(get_session)
) -> list[ApiToken]:
    return session.exec(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
    ).all()


@router.post("", response_model=TokenIssued, status_code=status.HTTP_201_CREATED)
def create_token(
    body: TokenCreate, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> TokenIssued:
    token, plaintext = issue_api_token(session, user, body.label, body.scope)
    return TokenIssued(
        id=token.id,
        label=token.label,
        prefix=token.prefix,
        scope=token.scope,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
        token=plaintext,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: str, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> None:
    revoke_api_token(session, user, token_id)
