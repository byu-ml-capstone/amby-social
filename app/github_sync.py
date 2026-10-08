import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings, settings
from app.github import AvailableRepository, GitHubClient
from app.models import (
    GitHubArtifact,
    GitHubArtifactKind,
    GitHubArtifactState,
    GitHubCIState,
    GitHubInstallation,
    GitHubReviewState,
    IntegrationDelivery,
    Project,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    Ticket,
    TicketGitLink,
    TicketStatus,
    utcnow,
)
from app.services import set_status, slugify

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TicketDevelopmentRow:
    repository_full_name: str
    kind: GitHubArtifactKind
    number: int | None
    title: str
    html_url: str | None
    author_login: str
    state: GitHubArtifactState | None
    review_state: GitHubReviewState | None
    ci_state: GitHubCIState
    occurred_at: datetime


def ticket_reference(project: Project, ticket: Ticket) -> str:
    return f"{project.key}-{ticket.ticket_number}"


def branch_command(project: Project, ticket: Ticket) -> str:
    reference = ticket_reference(project, ticket).lower()
    return f"git checkout -b feature/{reference}-{slugify(ticket.title)}"


def ticket_development(
    session: Session, ticket: Ticket
) -> list[TicketDevelopmentRow]:
    rows = session.exec(
        select(GitHubArtifact, ProjectGitHubRepository)
        .join(TicketGitLink, TicketGitLink.artifact_id == GitHubArtifact.id)
        .join(
            ProjectGitHubRepository,
            ProjectGitHubRepository.id == GitHubArtifact.repository_connection_id,
        )
        .where(
            TicketGitLink.ticket_id == ticket.id,
            ProjectGitHubRepository.active.is_(True),
        )
        .order_by(GitHubArtifact.occurred_at.desc())
    ).all()
    development = []
    for artifact, repository in rows:
        try:
            html_url = _validated_github_url(artifact.html_url, settings)
        except ValueError:
            html_url = None
        occurred_at = artifact.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        else:
            occurred_at = occurred_at.astimezone(UTC)
        development.append(
            TicketDevelopmentRow(
                repository_full_name=repository.full_name,
                kind=artifact.kind,
                number=artifact.number,
                title=artifact.title,
                html_url=html_url,
                author_login=artifact.author_login,
                state=artifact.state,
                review_state=artifact.review_state,
                ci_state=artifact.ci_state,
                occurred_at=occurred_at,
            )
        )
    return development


def save_project_repositories(
    session: Session,
    project: Project,
    installation: GitHubInstallation,
    repositories: list[AvailableRepository],
) -> list[ProjectGitHubRepository]:
    binding = session.get(ProjectGitHubConnection, project.id)
    if binding is None:
        binding = ProjectGitHubConnection(
            project_id=project.id,
            installation_id=installation.id,
        )
        session.add(binding)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            binding = session.get(ProjectGitHubConnection, project.id)
            if binding is None:
                raise
    else:
        session.refresh(binding)
    if binding.installation_id != installation.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Project already uses another GitHub installation",
        )

    now = utcnow()
    existing = {
        row.github_repository_id: row
        for row in session.exec(
            select(ProjectGitHubRepository).where(
                ProjectGitHubRepository.project_id == project.id
            )
        ).all()
    }
    selected = []
    for repository in repositories:
        row = existing.get(repository.id)
        if row is None:
            row = ProjectGitHubRepository(
                project_id=project.id,
                installation_id=installation.id,
                github_repository_id=repository.id,
                full_name=repository.full_name,
                html_url=repository.html_url,
                default_branch=repository.default_branch,
            )
        else:
            row.full_name = repository.full_name
            row.html_url = repository.html_url
            row.default_branch = repository.default_branch
            row.active = True
            row.disconnected_at = None
            row.updated_at = now
        session.add(row)
        selected.append(row)

    selected_ids = {repository.id for repository in repositories}
    for repository_id, row in existing.items():
        if repository_id not in selected_ids:
            row.active = False
            row.disconnected_at = now
            row.updated_at = now
            session.add(row)
    session.commit()
    for row in selected:
        session.refresh(row)
    return selected


def disconnect_project_github(session: Session, project: Project) -> None:
    now = utcnow()
    for row in session.exec(
        select(ProjectGitHubRepository).where(
            ProjectGitHubRepository.project_id == project.id
        )
    ).all():
        row.active = False
        row.disconnected_at = now
        row.updated_at = now
        session.add(row)
    session.commit()


def extract_ticket_numbers(
    project_key: str, texts: Iterable[str | None]
) -> set[int]:
    if not project_key:
        return set()
    pattern = re.compile(
        rf"(?<![A-Z0-9]){re.escape(project_key)}-([1-9][0-9]*)(?![A-Z0-9])",
        re.IGNORECASE,
    )
    numbers = set()
    for text in texts:
        if not isinstance(text, str):
            continue
        for match in pattern.finditer(text):
            try:
                numbers.add(int(match.group(1)))
            except ValueError:
                continue
    return numbers


def parse_github_datetime(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid GitHub datetime")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise ValueError("GitHub datetime must include a timezone")
    return parsed.astimezone(UTC)


def aggregate_review_state(reviews: Sequence[dict]) -> GitHubReviewState:
    ordered = []
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            continue
        try:
            submitted_at = parse_github_datetime(review.get("submitted_at"))
        except (TypeError, ValueError):
            continue
        ordered.append((submitted_at, index, review))

    effective: dict[str, str] = {}
    for _, _, review in sorted(ordered):
        user = review.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        state = review.get("state")
        if not isinstance(login, str) or not login or not isinstance(state, str):
            continue
        login = login.casefold()
        if state == "DISMISSED":
            effective.pop(login, None)
        elif state in {"APPROVED", "CHANGES_REQUESTED"}:
            effective[login] = state

    if "CHANGES_REQUESTED" in effective.values():
        return GitHubReviewState.CHANGES_REQUESTED
    if "APPROVED" in effective.values():
        return GitHubReviewState.APPROVED
    return GitHubReviewState.REVIEW_REQUIRED


def aggregate_ci_state(check_runs: Sequence[dict]) -> GitHubCIState:
    pending_statuses = {"queued", "in_progress", "waiting", "requested", "pending"}
    failed_conclusions = {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "startup_failure",
        "stale",
    }
    successful_conclusions = {"success", "neutral", "skipped"}
    checks = [
        check
        for check in check_runs
        if isinstance(check, dict)
        and (
            isinstance(check.get("status"), str)
            or isinstance(check.get("conclusion"), str)
        )
    ]
    if not checks:
        return GitHubCIState.NONE
    if any(check.get("conclusion") in failed_conclusions for check in checks):
        return GitHubCIState.FAILED
    if any(check.get("status") in pending_statuses for check in checks):
        return GitHubCIState.PENDING
    if all(
        check.get("status") == "completed"
        and check.get("conclusion") in successful_conclusions
        for check in checks
    ):
        return GitHubCIState.PASSED
    return GitHubCIState.NONE


def _validated_github_url(value: object, config: Settings) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid GitHub URL")
    try:
        parsed = urlparse(value)
        configured = urlparse(config.github_web_url)
        valid = (
            parsed.scheme == configured.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname == configured.hostname
            and (parsed.port or 443) == (configured.port or 443)
            and parsed.username is None
            and parsed.password is None
            and configured.username is None
            and configured.password is None
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("GitHub URL must use the configured HTTPS web origin")
    return value


def _matching_tickets(
    session: Session,
    connection: ProjectGitHubRepository,
    texts: Iterable[str | None],
) -> list[Ticket]:
    project = session.get(Project, connection.project_id)
    if project is None:
        return []
    numbers = extract_ticket_numbers(project.key, texts)
    if not numbers:
        return []
    return list(
        session.exec(
            select(Ticket).where(
                Ticket.project_id == connection.project_id,
                Ticket.ticket_number.in_(numbers),
            )
        ).all()
    )


def upsert_pull_request(
    session: Session,
    connection: ProjectGitHubRepository,
    pull: dict,
    config: Settings = settings,
) -> GitHubArtifact:
    if not isinstance(pull, dict):
        raise ValueError("invalid pull request payload")
    external_id = pull.get("node_id") or pull.get("id")
    number = pull.get("number")
    title = pull.get("title")
    user = pull.get("user")
    head = pull.get("head")
    author_login = user.get("login") if isinstance(user, dict) else None
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if (
        not isinstance(external_id, (str, int))
        or isinstance(external_id, bool)
        or not external_id
        or len(str(external_id)) > 255
        or not isinstance(number, int)
        or isinstance(number, bool)
        or number <= 0
        or not isinstance(title, str)
        or not isinstance(author_login, str)
        or not isinstance(head_sha, str)
        or len(head_sha) > 64
    ):
        raise ValueError("invalid pull request payload")

    external_id = str(external_id)
    html_url = _validated_github_url(pull.get("html_url"), config)
    occurred_at = parse_github_datetime(pull.get("updated_at"))
    state = (
        GitHubArtifactState.DRAFT
        if pull.get("draft")
        else GitHubArtifactState.MERGED
        if pull.get("merged_at")
        else GitHubArtifactState.CLOSED
        if pull.get("state") == "closed"
        else GitHubArtifactState.OPEN
    )
    artifact = session.exec(
        select(GitHubArtifact).where(
            GitHubArtifact.repository_connection_id == connection.id,
            GitHubArtifact.kind == GitHubArtifactKind.PULL_REQUEST,
            GitHubArtifact.external_id == external_id,
        )
    ).first()
    if artifact is None:
        artifact = GitHubArtifact(
            repository_connection_id=connection.id,
            kind=GitHubArtifactKind.PULL_REQUEST,
            external_id=external_id,
            number=number,
            title=title[:500],
            html_url=html_url,
            author_login=author_login[:255],
            state=state,
            review_state=GitHubReviewState.REVIEW_REQUIRED,
            head_sha=head_sha,
            occurred_at=occurred_at,
        )
    else:
        artifact.number = number
        artifact.title = title[:500]
        artifact.html_url = html_url
        artifact.author_login = author_login[:255]
        artifact.state = state
        if artifact.head_sha != head_sha:
            artifact.ci_state = GitHubCIState.NONE
        artifact.head_sha = head_sha
        artifact.occurred_at = occurred_at
        artifact.updated_at = utcnow()
    session.add(artifact)
    session.flush()
    return artifact


def reconcile_pull_request_links(
    session: Session,
    connection: ProjectGitHubRepository,
    artifact: GitHubArtifact,
    texts: Iterable[str | None],
) -> None:
    if artifact.repository_connection_id != connection.id:
        raise ValueError("artifact does not belong to repository connection")
    ticket_ids = {ticket.id for ticket in _matching_tickets(session, connection, texts)}
    existing = {
        link.ticket_id: link
        for link in session.exec(
            select(TicketGitLink).where(TicketGitLink.artifact_id == artifact.id)
        ).all()
    }
    for ticket_id, link in existing.items():
        if ticket_id not in ticket_ids:
            session.delete(link)
    for ticket_id in ticket_ids - existing.keys():
        session.add(TicketGitLink(ticket_id=ticket_id, artifact_id=artifact.id))


_PR_START_ACTIONS = {"opened", "reopened", "ready_for_review"}


def advance_tickets_for_pull_request(
    session: Session,
    connection: ProjectGitHubRepository,
    artifact: GitHubArtifact,
    action: str,
    texts: Iterable[str | None],
) -> None:
    """Move the tickets a PR mentions: start work on open, finish on merge.

    Never moves a ticket backward, so a manual move or a later push to the
    same PR is left alone. The board has no review column, so a review
    request changes nothing.
    """
    if action in _PR_START_ACTIONS and artifact.state in (
        GitHubArtifactState.OPEN,
        GitHubArtifactState.DRAFT,
    ):
        target, movable = TicketStatus.IN_PROGRESS, {TicketStatus.BACKLOG, TicketStatus.SELECTED}
    elif action == "closed" and artifact.state is GitHubArtifactState.MERGED:
        target, movable = TicketStatus.DONE, set(TicketStatus) - {TicketStatus.DONE}
    else:
        return
    for ticket in _matching_tickets(session, connection, texts):
        if ticket.status in movable:
            set_status(session, ticket, target, commit=False)


def sync_open_pull_requests(
    session: Session,
    connection: ProjectGitHubRepository,
    github: GitHubClient,
) -> int:
    installation = session.get(GitHubInstallation, connection.installation_id)
    if installation is None:
        raise ValueError("GitHub repository installation not found")

    imported = 0
    pulls = github.open_pull_requests(
        installation.github_installation_id,
        connection.full_name,
    )
    for pull in pulls:
        if not isinstance(pull, dict) or pull.get("state") != "open":
            continue
        head = pull.get("head")
        head_ref = head.get("ref") if isinstance(head, dict) else None
        texts = [pull.get("title"), pull.get("body"), head_ref]
        if not _matching_tickets(session, connection, texts):
            continue
        artifact = upsert_pull_request(session, connection, pull)
        reconcile_pull_request_links(session, connection, artifact, texts)
        artifact.review_state = aggregate_review_state(
            github.pull_request_reviews(
                installation.github_installation_id,
                connection.full_name,
                artifact.number,
            )
        )
        artifact.ci_state = aggregate_ci_state(
            github.check_runs(
                installation.github_installation_id,
                connection.full_name,
                artifact.head_sha,
            )
        )
        artifact.updated_at = utcnow()
        session.add(artifact)
        imported += 1
    return imported


def insert_linked_commit(
    session: Session,
    connection: ProjectGitHubRepository,
    commit: dict,
    config: Settings = settings,
) -> GitHubArtifact | None:
    if not isinstance(commit, dict):
        return None
    sha = commit.get("id", "")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
        return None
    sha = sha.lower()
    existing = session.exec(
        select(GitHubArtifact).where(
            GitHubArtifact.repository_connection_id == connection.id,
            GitHubArtifact.kind == GitHubArtifactKind.COMMIT,
            GitHubArtifact.external_id == sha,
        )
    ).first()
    if existing is not None:
        return existing

    message = commit.get("message")
    if not isinstance(message, str):
        return None
    tickets = _matching_tickets(session, connection, [message])
    if not tickets:
        return None
    html_url = f"{_validated_github_url(connection.html_url, config).rstrip('/')}/commit/{sha}"
    try:
        occurred_at = parse_github_datetime(commit.get("timestamp"))
    except (TypeError, ValueError):
        return None
    author = commit.get("author")
    committer = commit.get("committer")
    author = author if isinstance(author, dict) else {}
    committer = committer if isinstance(committer, dict) else {}
    author_login = (
        author.get("username")
        or author.get("name")
        or committer.get("username")
        or committer.get("name")
        or ""
    )
    if not isinstance(author_login, str):
        author_login = ""
    artifact = GitHubArtifact(
        repository_connection_id=connection.id,
        kind=GitHubArtifactKind.COMMIT,
        external_id=sha,
        title=message.splitlines()[0][:500],
        html_url=html_url,
        author_login=author_login[:255],
        occurred_at=occurred_at,
    )
    session.add(artifact)
    session.flush()
    for ticket in tickets:
        session.add(TicketGitLink(ticket_id=ticket.id, artifact_id=artifact.id))
    return artifact


_PULL_REQUEST_ACTIONS = {
    "opened",
    "edited",
    "synchronize",
    "reopened",
    "ready_for_review",
    "converted_to_draft",
    "closed",
}
_REVIEW_ACTIONS = {"submitted", "edited", "dismissed"}
_CHECK_RUN_ACTIONS = {"created", "rerequested", "completed", "requested_action"}


def _github_id(payload: dict, key: str) -> int | None:
    value = payload.get(key)
    value = value.get("id") if isinstance(value, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _repository_connections(session: Session, payload: dict) -> list[ProjectGitHubRepository]:
    repository_id = _github_id(payload, "repository")
    installation_id = _github_id(payload, "installation")
    if repository_id is None or installation_id is None:
        return []
    return list(
        session.exec(
            select(ProjectGitHubRepository)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == ProjectGitHubRepository.installation_id,
            )
            .where(
                ProjectGitHubRepository.github_repository_id == repository_id,
                GitHubInstallation.github_installation_id == installation_id,
                ProjectGitHubRepository.active.is_(True),
            )
        ).all()
    )


def _pull_number(payload: dict) -> int | None:
    pull = payload.get("pull_request")
    number = pull.get("number") if isinstance(pull, dict) else None
    valid = isinstance(number, int) and not isinstance(number, bool) and number > 0
    return number if valid else None


def _existing_pull(
    session: Session,
    connection: ProjectGitHubRepository,
    number: int,
) -> GitHubArtifact | None:
    return session.exec(
        select(GitHubArtifact).where(
            GitHubArtifact.repository_connection_id == connection.id,
            GitHubArtifact.kind == GitHubArtifactKind.PULL_REQUEST,
            GitHubArtifact.number == number,
        )
    ).first()


def _disable_repositories(repositories: Iterable[ProjectGitHubRepository]) -> None:
    now = utcnow()
    for repository in repositories:
        repository.active = False
        repository.disconnected_at = None
        repository.updated_at = now


def dispatch_github_event(
    session: Session,
    event_type: str,
    payload: dict,
    github: GitHubClient,
) -> None:
    action = payload.get("action")
    if action is not None and not isinstance(action, str):
        return
    connections = _repository_connections(session, payload)
    if event_type == "pull_request" and action in _PULL_REQUEST_ACTIONS:
        pull = payload.get("pull_request")
        if not isinstance(pull, dict):
            return
        head = pull.get("head")
        head_ref = head.get("ref") if isinstance(head, dict) else None
        for connection in connections:
            artifact = upsert_pull_request(session, connection, pull)
            texts = [pull.get("title"), pull.get("body"), head_ref]
            reconcile_pull_request_links(session, connection, artifact, texts)
            advance_tickets_for_pull_request(session, connection, artifact, action, texts)
        return
    if event_type == "pull_request_review" and action in _REVIEW_ACTIONS:
        number = _pull_number(payload)
        if number is None:
            return
        for connection in connections:
            artifact = _existing_pull(session, connection, number)
            if artifact is None:
                continue
            try:
                reviews = github.pull_request_reviews(
                    _github_id(payload, "installation"),
                    connection.full_name,
                    number,
                )
            except httpx.HTTPError:
                logger.warning(
                    "GitHub review refresh failed repository=%s pull_request=%s",
                    connection.full_name,
                    number,
                )
                continue
            artifact.review_state = aggregate_review_state(reviews)
            artifact.updated_at = utcnow()
            session.add(artifact)
        return
    if event_type == "check_run" and action in _CHECK_RUN_ACTIONS:
        check_run = payload.get("check_run")
        sha = check_run.get("head_sha") if isinstance(check_run, dict) else None
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
            return
        installation_id = _github_id(payload, "installation")
        for connection in connections:
            pull_numbers: list[int] = []
            try:
                pulls = github.pull_requests_for_commit(
                    installation_id,
                    connection.full_name,
                    sha,
                )
                for pull in pulls:
                    number = pull.get("number") if isinstance(pull, dict) else None
                    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
                        pull_numbers.append(number)
                artifacts = [
                    artifact
                    for number in pull_numbers
                    if (artifact := _existing_pull(session, connection, number)) is not None
                    and artifact.head_sha == sha
                ]
                if not artifacts:
                    continue
                checks = github.check_runs(installation_id, connection.full_name, sha)
            except httpx.HTTPError:
                logger.warning(
                    "GitHub check refresh failed repository=%s pull_request=%s",
                    connection.full_name,
                    ",".join(map(str, pull_numbers)) or "unknown",
                )
                continue
            ci_state = aggregate_ci_state(checks)
            for artifact in artifacts:
                artifact.ci_state = ci_state
                artifact.updated_at = utcnow()
                session.add(artifact)
        return
    if event_type == "push":
        commits = payload.get("commits")
        if not isinstance(commits, list):
            return
        for connection in connections:
            for commit in commits:
                insert_linked_commit(session, connection, commit)
        return
    if event_type == "installation_repositories" and action == "removed":
        removed = payload.get("repositories_removed")
        if not isinstance(removed, list):
            return
        repository_ids = {
            repository_id
            for repository in removed
            if isinstance(repository, dict)
            and isinstance((repository_id := repository.get("id")), int)
            and not isinstance(repository_id, bool)
            and repository_id > 0
        }
        installation_id = _github_id(payload, "installation")
        if installation_id is None or not repository_ids:
            return
        repositories = session.exec(
            select(ProjectGitHubRepository)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == ProjectGitHubRepository.installation_id,
            )
            .where(
                GitHubInstallation.github_installation_id == installation_id,
                ProjectGitHubRepository.github_repository_id.in_(repository_ids),
            )
        ).all()
        _disable_repositories(repositories)
        return
    if event_type == "installation" and action in {"deleted", "suspend"}:
        installation_id = _github_id(payload, "installation")
        if installation_id is None:
            return
        repositories = session.exec(
            select(ProjectGitHubRepository)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == ProjectGitHubRepository.installation_id,
            )
            .where(GitHubInstallation.github_installation_id == installation_id)
        ).all()
        _disable_repositories(repositories)


def claim_github_delivery(session: Session, delivery: IntegrationDelivery) -> bool:
    try:
        with session.begin_nested():
            session.add(delivery)
            session.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        sqlite_columns = "integration_delivery.provider, integration_delivery.delivery_id"
        if constraint not in {
            "uq_integration_delivery_provider_delivery_id",
            "uq_integration_delivery_provider",
        } and sqlite_columns not in str(exc.orig):
            raise
        return False
    return True


def _begin_sqlite_transaction(session: Session) -> None:
    connection = session.connection()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.dbapi_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN")


def process_github_delivery(
    session: Session,
    delivery_id: str,
    event_type: str,
    payload: dict,
    github: GitHubClient,
) -> bool:
    delivery = IntegrationDelivery(
        provider="GITHUB",
        delivery_id=delivery_id,
        event_type=event_type,
    )
    try:
        _begin_sqlite_transaction(session)
        if not claim_github_delivery(session, delivery):
            session.rollback()
            return False
        dispatch_github_event(session, event_type, payload, github)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return True
