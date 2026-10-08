from sqlmodel import select

from app.github_sync import sync_open_pull_requests
from app.models import GitHubArtifact
from tests.test_github_sync import _pull
from tests.test_github_sync import github_sync_world as github_sync_world


class FakeGitHub:
    def __init__(self):
        self.review_calls = []
        self.check_calls = []

    def open_pull_requests(self, installation_id, full_name):
        assert (installation_id, full_name) == (7001, "acme/api")
        return [
            _pull(title="PAY-1 first"),
            {
                **_pull(title="second", body="PAY-2"),
                "id": 9002,
                "node_id": "PR_node_9002",
                "number": 18,
                "head": {"ref": "feature/second", "sha": "b" * 40},
            },
            {
                **_pull(title="unlinked", body=""),
                "id": 9003,
                "node_id": "PR_node_9003",
                "number": 19,
                "head": {"ref": "feature/unlinked", "sha": "c" * 40},
            },
        ]

    def pull_request_reviews(self, installation_id, full_name, number):
        self.review_calls.append((installation_id, full_name, number))
        return []

    def check_runs(self, installation_id, full_name, sha):
        self.check_calls.append((installation_id, full_name, sha))
        return []


def test_initial_sync_imports_only_linked_open_pull_requests(
    session, github_sync_world
):
    github = FakeGitHub()
    count = sync_open_pull_requests(session, github_sync_world.connection, github)
    session.commit()
    assert count == 2
    assert [call[2] for call in github.review_calls] == [17, 18]
    assert [call[2] for call in github.check_calls] == ["a" * 40, "b" * 40]
    assert sync_open_pull_requests(session, github_sync_world.connection, github) == 2
    assert len(session.exec(select(GitHubArtifact)).all()) == 2


def test_initial_sync_ignores_non_open_pull_requests(session, github_sync_world):
    github = FakeGitHub()
    github.open_pull_requests = lambda installation_id, full_name: [
        {**_pull(title="PAY-1 closed"), "state": "closed"}
    ]

    assert sync_open_pull_requests(session, github_sync_world.connection, github) == 0
    assert github.review_calls == []
    assert github.check_calls == []
    assert session.exec(select(GitHubArtifact)).all() == []


def test_initial_sync_ignores_oversized_reference_and_imports_valid_pull(
    session, github_sync_world
):
    github = FakeGitHub()
    github.open_pull_requests = lambda installation_id, full_name: [
        {
            **_pull(title="oversized", body=f"PAY-{'9' * 5000}"),
            "id": 9004,
            "node_id": "PR_node_9004",
            "number": 20,
            "head": {"ref": "feature/oversized", "sha": "d" * 40},
        },
        {
            **_pull(title="PAY-1 valid", body=""),
            "id": 9005,
            "node_id": "PR_node_9005",
            "number": 21,
            "head": {"ref": "feature/valid", "sha": "e" * 40},
        },
    ]

    assert sync_open_pull_requests(session, github_sync_world.connection, github) == 1
    session.commit()
    assert [artifact.title for artifact in session.exec(select(GitHubArtifact)).all()] == [
        "PAY-1 valid"
    ]
