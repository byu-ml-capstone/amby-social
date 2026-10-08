import logging
import time
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.github import AvailableRepository, GitHubClient, github_is_configured

USER_TOKEN = "ghu_test_user_token"
INSTALLATION_TOKEN = "ghs_test_installation_token"


@pytest.fixture
def configured_settings():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return Settings(
        _env_file=None,
        session_secret="test-session-secret",
        github_app_id="12345",
        github_app_slug="kanban-flow-test",
        github_client_id="client-id",
        github_client_secret="client-secret",
        github_private_key=pem,
        github_webhook_secret="webhook-secret",
        github_api_url="https://api.github.com",
        github_web_url="https://github.com",
    )


def _assert_timeout(request):
    assert request.extensions["timeout"] == {
        "connect": 10.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }


def _assert_api_headers(request, token):
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    _assert_timeout(request)


def test_configuration_requires_every_github_setting(configured_settings):
    assert github_is_configured(configured_settings) is True
    required = (
        "github_app_id",
        "github_app_slug",
        "github_client_id",
        "github_client_secret",
        "github_private_key",
        "github_webhook_secret",
    )
    for field in required:
        assert github_is_configured(configured_settings.model_copy(update={field: ""})) is False


def test_exchange_code_posts_form_and_returns_non_empty_token(configured_settings):
    def handler(request):
        assert request.method == "POST"
        assert request.url == "https://github.com/login/oauth/access_token"
        assert request.headers["Accept"] == "application/json"
        assert parse_qs(request.content.decode()) == {
            "client_id": ["client-id"],
            "client_secret": ["client-secret"],
            "code": ["oauth-code"],
        }
        _assert_timeout(request)
        return httpx.Response(200, json={"access_token": USER_TOKEN})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        github = GitHubClient(configured_settings, transport)
        assert github.exchange_code("oauth-code") == USER_TOKEN


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=b"not-json"), "GitHub OAuth returned invalid JSON"),
        (httpx.Response(200, json={"error": "access_denied"}), "access_denied"),
        (httpx.Response(200, json={}), "missing access token"),
        (httpx.Response(200, json={"access_token": ""}), "missing access token"),
    ],
)
def test_exchange_code_rejects_invalid_responses(configured_settings, response, message):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as transport:
        with pytest.raises(ValueError, match=message):
            GitHubClient(configured_settings, transport).exchange_code("oauth-code")


def test_exchange_code_raises_for_http_errors(configured_settings):
    response = httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as transport:
        with pytest.raises(httpx.HTTPStatusError):
            GitHubClient(configured_settings, transport).exchange_code("oauth-code")


def test_verify_user_installation_uses_only_user_bearer(configured_settings):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/user/installations/7001/repositories"
        _assert_api_headers(request, USER_TOKEN)
        return httpx.Response(200, json={"repositories": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        assert (
            GitHubClient(configured_settings, transport).verify_user_installation(USER_TOKEN, 7001)
            is None
        )


def test_installation_uses_rs256_app_jwt(configured_settings):
    before = int(time.time())

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/app/installations/7001"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        public_key = serialization.load_pem_private_key(
            configured_settings.github_private_key.encode(), password=None
        ).public_key()
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_iat": False},
        )
        assert claims["iss"] == "12345"
        assert claims["exp"] - claims["iat"] == 600
        assert before - 60 <= claims["iat"] <= int(time.time()) - 60
        assert jwt.get_unverified_header(token)["alg"] == "RS256"
        assert request.headers["Accept"] == "application/vnd.github+json"
        _assert_timeout(request)
        return httpx.Response(200, json={"id": 7001, "account": {"login": "acme"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = GitHubClient(configured_settings, transport).installation(7001)
    assert result["id"] == 7001


def test_repositories_get_private_token_and_follow_all_pages(configured_settings):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/app/installations/7001/access_tokens":
            assert request.method == "POST"
            assert request.headers["Authorization"].startswith("Bearer ey")
            _assert_timeout(request)
            return httpx.Response(201, json={"token": INSTALLATION_TOKEN})
        _assert_api_headers(request, INSTALLATION_TOKEN)
        assert request.url.path == "/installation/repositories"
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {
                            "id": 502,
                            "full_name": "acme/web",
                            "html_url": "https://github.com/acme/web",
                            "default_branch": "trunk",
                        }
                    ]
                },
            )
        assert request.url.params["per_page"] == "100"
        return httpx.Response(
            200,
            json={
                "repositories": [
                    {
                        "id": 501,
                        "full_name": "acme/api",
                        "html_url": "https://github.com/acme/api",
                        "default_branch": "main",
                    }
                ]
            },
            headers={
                "Link": '<https://api.github.com/installation/repositories?page=2>; rel="next"'
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        repositories = GitHubClient(configured_settings, transport).repositories(7001)

    assert repositories == [
        AvailableRepository(501, "acme/api", "https://github.com/acme/api", "main"),
        AvailableRepository(502, "acme/web", "https://github.com/acme/web", "trunk"),
    ]
    assert len(requests) == 3


@pytest.mark.parametrize(
    ("method", "args", "first_path", "item_key"),
    [
        ("open_pull_requests", (7001, "acme/api"), "/repos/acme/api/pulls", None),
        (
            "pull_request_reviews",
            (7001, "acme/api", 17),
            "/repos/acme/api/pulls/17/reviews",
            None,
        ),
        (
            "check_runs",
            (7001, "acme/api", "deadbeef"),
            "/repos/acme/api/commits/deadbeef/check-runs",
            "check_runs",
        ),
        (
            "pull_requests_for_commit",
            (7001, "acme/api", "deadbeef"),
            "/repos/acme/api/commits/deadbeef/pulls",
            None,
        ),
    ],
)
def test_installation_lists_follow_every_next_page(
    configured_settings, method, args, first_path, item_key
):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/app/installations/7001/access_tokens":
            return httpx.Response(201, json={"token": INSTALLATION_TOKEN})
        _assert_api_headers(request, INSTALLATION_TOKEN)
        if request.url.params.get("page") == "2":
            body = [{"id": 2}]
            return httpx.Response(200, json={item_key: body} if item_key else body)
        assert request.url.path == first_path
        assert request.url.params["per_page"] == "100"
        if method == "open_pull_requests":
            assert request.url.params["state"] == "open"
        body = [{"id": 1}]
        return httpx.Response(
            200,
            json={item_key: body} if item_key else body,
            headers={"Link": f'<https://api.github.com{first_path}?page=2>; rel="next"'},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = getattr(GitHubClient(configured_settings, transport), method)(*args)

    assert result == [{"id": 1}, {"id": 2}]
    assert len(requests) == 3


def test_path_components_are_percent_encoded(configured_settings):
    paths = []

    def handler(request):
        if request.url.path == "/app/installations/7001/access_tokens":
            return httpx.Response(201, json={"token": INSTALLATION_TOKEN})
        paths.append(request.url.raw_path.decode().split("?", 1)[0])
        body = {"check_runs": []} if request.url.path.endswith("/check-runs") else []
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        github = GitHubClient(configured_settings, transport)
        github.open_pull_requests(7001, "acme space/api#v1")
        github.pull_request_reviews(7001, "acme space/api#v1", 17)
        github.check_runs(7001, "acme space/api#v1", "dead/beef")
        github.pull_requests_for_commit(7001, "acme space/api#v1", "dead/beef")

    assert paths == [
        "/repos/acme%20space/api%23v1/pulls",
        "/repos/acme%20space/api%23v1/pulls/17/reviews",
        "/repos/acme%20space/api%23v1/commits/dead%2Fbeef/check-runs",
        "/repos/acme%20space/api%23v1/commits/dead%2Fbeef/pulls",
    ]


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/repos?page=2",
        "//evil.example/repos?page=2",
        "http://api.github.com/repos?page=2",
        "https://api.github.com:444/repos?page=2",
    ],
)
def test_pagination_rejects_next_link_outside_api_origin(configured_settings, next_url):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) > 2:
            pytest.fail("authorization-bearing request followed an unsafe next link")
        assert request.url.host == "api.github.com"
        if request.url.path == "/app/installations/7001/access_tokens":
            return httpx.Response(201, json={"token": INSTALLATION_TOKEN})
        return httpx.Response(
            200,
            json=[],
            headers={"Link": f'<{next_url}>; rel="next"'},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        github = GitHubClient(configured_settings, transport)
        with pytest.raises(ValueError, match="pagination left configured API origin"):
            github.open_pull_requests(7001, "acme/api")
    assert len(requests) == 2
    assert all(request.url.host == "api.github.com" for request in requests)


def test_api_methods_raise_for_non_success(configured_settings):
    def handler(request):
        if request.url.path == "/app/installations/7001/access_tokens":
            return httpx.Response(201, json={"token": INSTALLATION_TOKEN})
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        with pytest.raises(httpx.HTTPStatusError):
            GitHubClient(configured_settings, transport).open_pull_requests(7001, "acme/api")


def test_tokens_are_not_logged(configured_settings, caplog):
    def handler(request):
        if request.url.host == "github.com":
            return httpx.Response(200, json={"access_token": USER_TOKEN})
        if request.url.path == "/app/installations/7001/access_tokens":
            return httpx.Response(201, json={"token": INSTALLATION_TOKEN})
        return httpx.Response(200, json=[])

    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        github = GitHubClient(configured_settings, transport)
        github.exchange_code("oauth-code")
        github.verify_user_installation(USER_TOKEN, 7001)
        github.open_pull_requests(7001, "acme/api")

    assert USER_TOKEN not in caplog.text
    assert INSTALLATION_TOKEN not in caplog.text


def test_close_only_closes_internally_created_client(configured_settings):
    injected = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    github = GitHubClient(configured_settings, injected)
    github.close()
    assert injected.is_closed is False
    injected.close()

    owned = GitHubClient(configured_settings)
    owned.close()
    assert owned._client.is_closed is True
