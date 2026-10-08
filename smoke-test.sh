#!/usr/bin/env bash
# smoke-test.sh — hit the important endpoints and fail loudly if one is down.
#
#   ./smoke-test.sh                       local: builds + starts compose, tests localhost:8000
#   ./smoke-test.sh http://<your-app>.ml-capstone.cs.byu.edu
#                                         remote: tests a deployed Coolify instance

set -euo pipefail
cd "$(dirname "$0")"

if [ $# -eq 0 ]; then
    MODE=local
    BASE_URL="http://localhost:8000"
else
    MODE=remote
    BASE_URL="${1%/}"
fi

if [ "$MODE" = "local" ]; then
    # Stubs for the compose interpolation; Coolify populates these in production.
    export SERVICE_FQDN_WEB="$BASE_URL"
    export APP_HOST=localhost
    export SESSION_SECRET="${SESSION_SECRET:-local-smoke-test-secret}"

    echo "=== local mode: building + starting web (docker compose) ==="
    docker compose down --remove-orphans >/dev/null 2>&1 || true
    docker compose up -d --build
else
    echo "=== remote mode: smoke-testing $BASE_URL ==="
fi

echo -n "waiting for /health "
for _ in $(seq 1 60); do
    if curl -sSf "$BASE_URL/health" >/dev/null 2>&1; then
        echo " ready"
        break
    fi
    echo -n "."
    sleep 1
done

check() {
    local path=$1 expected=$2
    local code
    code=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL$path")
    if [ "$code" = "$expected" ]; then
        echo "ok    $path -> $code"
    else
        echo "FAIL  $path -> $code (expected $expected)"
        exit 1
    fi
}

check /health 200
check /login 200
check /register 200
check /api/v1/projects 401
check /static/app.css 200

echo
echo "Smoke test passed: $BASE_URL"
[ "$MODE" = "local" ] && echo "Still running. Stop with: docker compose down"
exit 0
