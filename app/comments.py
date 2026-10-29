"""Ticket comments: who may write, edit and delete them, and who gets a mention."""

from fastapi import BackgroundTasks
from sqlmodel import Session, select

from app.errors import Forbidden, Invalid, NotFound
from app.models import Project, ProjectMember, Role, Ticket, TicketComment, User, utcnow
from app.notifications import schedule_comment_mention

COMMENT_MAX_LENGTH = 5000


def can_edit(comment: TicketComment, user: User) -> bool:
    return comment.author_id == user.id


def can_delete(comment: TicketComment, user: User, role: Role) -> bool:
    return comment.author_id == user.id or role == Role.OWNER


def get_comment(session: Session, ticket: Ticket, comment_id: str) -> TicketComment:
    comment = session.get(TicketComment, comment_id)
    if comment is None or comment.ticket_id != ticket.id:
        raise NotFound("Comment not found")
    return comment


def _mentioned_members(session: Session, project: Project, mention_ids: list[str]) -> list[User]:
    mention_ids = list(dict.fromkeys(mention_ids))
    if not mention_ids:
        return []
    users = session.exec(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id, User.id.in_(mention_ids))
    ).all()
    if len(users) != len(mention_ids):
        raise Invalid("Mentions must be project members")
    by_id = {user.id: user for user in users}
    return [by_id[user_id] for user_id in mention_ids]


def _clean_input(
    session: Session, project: Project, body: str, mention_ids: list[str]
) -> tuple[str, list[User]]:
    body = body.strip()
    if not body:
        raise Invalid("Comment cannot be empty")
    if len(body) > COMMENT_MAX_LENGTH:
        raise Invalid(
            f"Comment must be at most {COMMENT_MAX_LENGTH} characters",
        )
    return body, _mentioned_members(session, project, mention_ids)


def add_comment(
    session: Session,
    project: Project,
    ticket: Ticket,
    author: User,
    body: str,
    mention_ids: list[str],
    tasks: BackgroundTasks | None = None,
) -> TicketComment:
    body, mentioned = _clean_input(session, project, body, mention_ids)
    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=author.id,
        body=body,
        mentioned_user_ids=[user.id for user in mentioned],
    )
    session.add(comment)
    session.commit()
    if tasks is not None:
        schedule_comment_mention(tasks, session, project, ticket, author, mentioned, body)
    return comment


def edit_comment(
    session: Session,
    project: Project,
    ticket: Ticket,
    comment: TicketComment,
    editor: User,
    body: str,
    mention_ids: list[str],
    tasks: BackgroundTasks | None = None,
) -> TicketComment:
    """Only the author edits; only people newly mentioned by the edit are notified."""
    if not can_edit(comment, editor):
        raise Forbidden("Only the author can edit this comment")
    body, mentioned = _clean_input(session, project, body, mention_ids)
    previous = set(comment.mentioned_user_ids)
    comment.body = body
    comment.mentioned_user_ids = [user.id for user in mentioned]
    comment.updated_at = utcnow()
    session.add(comment)
    session.commit()
    if tasks is not None:
        new_mentions = [user for user in mentioned if user.id not in previous]
        schedule_comment_mention(tasks, session, project, ticket, editor, new_mentions, body)
    return comment


def delete_comment(session: Session, comment: TicketComment, user: User, role: Role) -> None:
    if not can_delete(comment, user, role):
        raise Forbidden("Cannot delete this comment")
    session.delete(comment)
    session.commit()
