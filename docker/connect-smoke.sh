#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${repo_root}/.env"
connect_image="rstudio/rstudio-connect:jammy-2025.06.0"
proxy_image="nginx:1.27-alpine"
host_port="39391"
server_url="http://127.0.0.1:${host_port}"
run_id="$$"
connect_container_name="access-registry-connect-smoke-${run_id}"
proxy_container_name="access-registry-connect-cookie-proxy-${run_id}"
network_name="access-registry-connect-smoke-${run_id}"
data_volume="access-registry-connect-smoke-data-${run_id}"
smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/access-registry-connect-smoke.XXXXXX")"
bundle_dir="${smoke_root}/bundle"
bootstrap_key="${smoke_root}/connect-bootstrap.key"
cookie_jar="${smoke_root}/cookies.txt"
confirmation_cookie_jar="${smoke_root}/confirmation-cookies.txt"
login_page="${smoke_root}/login.html"
login_headers="${smoke_root}/login-headers.txt"
login_response="${smoke_root}/login-response.html"
profile_page="${smoke_root}/profile.html"
deactivation_verified=0
connect_started=0
proxy_started=0
network_created=0

log() {
    printf '[connect-smoke] %s\n' "$1"
}

show_app_diagnostics() {
    if [[ "${connect_started}" -ne 1 ]]; then
        return
    fi
    log "recent secret-free application diagnostics"
    docker exec "${connect_container_name}" /bin/bash -lc \
        'grep -R -h "\[access-registry:dev\]" /data/jobs 2>/dev/null | tail -n 40' || true
}

cleanup() {
    original_status=$?
    trap - EXIT INT TERM
    set +e

    if [[ "${connect_started}" -eq 1 ]] && \
        docker container inspect "${connect_container_name}" >/dev/null 2>&1; then
        log "deactivating Connect license"
        for attempt in 1 2 3; do
            if docker exec "${connect_container_name}" \
                /opt/rstudio-connect/bin/license-manager deactivate >/dev/null 2>&1; then
                if docker exec "${connect_container_name}" /bin/bash -lc \
                    'test ! -d /var/lib/.local || ! find /var/lib/.local -type f -size +0c -print -quit | grep -q .' \
                    >/dev/null 2>&1; then
                    deactivation_verified=1
                    break
                fi
            fi
            log "license deactivation retry ${attempt}"
        done

    fi

    if [[ "${proxy_started}" -eq 1 ]]; then
        docker rm -f "${proxy_container_name}" >/dev/null 2>&1
    fi
    if [[ "${connect_started}" -eq 1 ]]; then
        docker stop --time 120 "${connect_container_name}" >/dev/null 2>&1
        docker rm -f "${connect_container_name}" >/dev/null 2>&1
    fi

    docker volume rm "${data_volume}" >/dev/null 2>&1
    if [[ "${network_created}" -eq 1 ]]; then
        docker network rm "${network_name}" >/dev/null 2>&1
    fi

    case "${smoke_root}" in
        /tmp/access-registry-connect-smoke.*|/private/tmp/access-registry-connect-smoke.*|/var/folders/*/access-registry-connect-smoke.*|/private/var/folders/*/access-registry-connect-smoke.*)
            rm -rf -- "${smoke_root}"
            ;;
        *)
            log "refusing to remove unexpected temporary path: ${smoke_root}"
            original_status=1
            ;;
    esac

    if docker ps -a --format '{{.Names}}' | grep -Fxq "${connect_container_name}"; then
        log "cleanup failed: Connect test container still exists"
        original_status=1
    fi
    if docker ps -a --format '{{.Names}}' | grep -Fxq "${proxy_container_name}"; then
        log "cleanup failed: cookie proxy test container still exists"
        original_status=1
    fi
    if docker volume inspect "${data_volume}" >/dev/null 2>&1; then
        log "cleanup failed: test data volume still exists"
        original_status=1
    fi
    if docker network inspect "${network_name}" >/dev/null 2>&1; then
        log "cleanup failed: test network still exists"
        original_status=1
    fi
    if [[ "${connect_started}" -eq 1 && "${deactivation_verified}" -ne 1 ]]; then
        log "cleanup failed: license deactivation could not be verified"
        original_status=1
    elif [[ "${connect_started}" -eq 1 ]]; then
        log "license deactivation verified"
    fi

    if [[ "${original_status}" -eq 0 ]]; then
        log "containers, network, volume, and temporary bundle removed"
    fi
    exit "${original_status}"
}
trap cleanup EXIT INT TERM

if [[ ! -f "${env_file}" ]]; then
    log ".env is missing"
    exit 2
fi

license_line="$(grep -m 1 -E '^[[:space:]]*CONNECT_LICENSE[[:space:]]*=' "${env_file}" || true)"
connect_license="${license_line#*=}"
connect_license="${connect_license#\"}"
connect_license="${connect_license%\"}"
connect_license="${connect_license#\'}"
connect_license="${connect_license%\'}"
if [[ ! "${connect_license}" =~ ^[[:alnum:]]+(-[[:alnum:]]+)+$ ]]; then
    log "CONNECT_LICENSE is missing or is not a license-key value"
    exit 2
fi

for command in curl docker jq lsof openssl rsync; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        log "required command is missing: ${command}"
        exit 2
    fi
done
if [[ ! -x "${repo_root}/.venv/bin/python" || ! -x "${repo_root}/.venv/bin/rsconnect" ]]; then
    log "project virtual environment or rsconnect CLI is missing"
    exit 2
fi
if lsof -nP -iTCP:"${host_port}" -sTCP:LISTEN >/dev/null 2>&1; then
    log "host port ${host_port} is already in use"
    exit 2
fi

mkdir -p "${bundle_dir}"
rsync -a \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='.env' \
    --exclude='.pytest_cache/' \
    --exclude='.ruff_cache/' \
    --exclude='__pycache__/' \
    --exclude='*.db' \
    --exclude='*.sqlite3' \
    --exclude='deployment/' \
    --exclude='rsconnect-python/' \
    --exclude='.rsconnect-python/' \
    "${repo_root}/" "${bundle_dir}/"
mkdir -p "${bundle_dir}/deployment"
openssl rand -out "${bootstrap_key}" -base64 48
chmod 600 "${bootstrap_key}"

smoke_admin_password="$(openssl rand -base64 30 | tr -d '\n')"
connect_browser_username="smokeuser${run_id}"
connect_browser_password="$(openssl rand -base64 30 | tr -d '\n')"
export APP_ENV=development
export APP_NAME='Access Registry Connect Docker Smoke'
export PUBLIC_BASE_URL="${server_url}"
export DATABASE_URL='sqlite:///./deployment/connect-smoke.db'
export JWT_SECRET="$(openssl rand -base64 48 | tr -d '\n')"
export SESSION_PEPPER="$(openssl rand -base64 48 | tr -d '\n')"
export CSRF_SECRET="$(openssl rand -base64 48 | tr -d '\n')"
export API_TOKEN_ACTIVE_KEY_ID='connect-smoke-v1'
api_encryption_key="$(openssl rand -base64 32 | tr -d '\n')"
export API_TOKEN_ENCRYPTION_KEYS="{\"connect-smoke-v1\":\"${api_encryption_key}\"}"
export AUTHENTICATION_MODE=local_password
export COOKIE_SECURE=false
export COOKIE_PATH=auto
export ALLOWED_EMAIL_DOMAINS=example.gov
export RATE_LIMIT_ENABLED=true
export EMAIL_BACKEND=console
export ACCESS_REGISTRY_DEV_TRACE=1

log "building a fresh seeded SQLite bundle"
(
    cd "${bundle_dir}"
    "${repo_root}/.venv/bin/python" -m app migrate >/dev/null
    ADMIN_BOOTSTRAP_PASSWORD="${smoke_admin_password}" \
        "${repo_root}/.venv/bin/python" -m app create-admin \
        --email admin@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD >/dev/null
    "${repo_root}/.venv/bin/python" -m hedron build >/dev/null
)

docker volume create "${data_volume}" >/dev/null
docker network create "${network_name}" >/dev/null
network_created=1
log "starting licensed Connect 2025.06.0 with Python 3.11.7"
docker run -d \
    --platform linux/amd64 \
    --privileged \
    --stop-timeout 120 \
    --name "${connect_container_name}" \
    --hostname "${connect_container_name}" \
    --network "${network_name}" \
    --network-alias connect \
    -e RSC_LICENSE="${connect_license}" \
    -v "${data_volume}:/data" \
    -v "${repo_root}/docker/connect-smoke.gcfg:/etc/rstudio-connect/rstudio-connect.gcfg:ro" \
    -v "${bootstrap_key}:/run/secrets/connect-bootstrap.key:ro" \
    "${connect_image}" >/dev/null
connect_started=1
unset connect_license

log "starting the header-overwriting cookie proxy"
docker run -d \
    --name "${proxy_container_name}" \
    --network "${network_name}" \
    -p "127.0.0.1:${host_port}:8080" \
    -v "${repo_root}/docker/connect-cookie-proxy.conf:/etc/nginx/nginx.conf:ro" \
    "${proxy_image}" >/dev/null
proxy_started=1

ready=0
for _ in {1..90}; do
    if curl --silent --fail "${server_url}/__api__/server_settings" >/dev/null 2>&1; then
        ready=1
        break
    fi
    if ! docker ps --format '{{.Names}}' | grep -Fxq "${connect_container_name}"; then
        log "Connect stopped during startup"
        docker logs --tail 120 "${connect_container_name}" || true
        exit 1
    fi
    if ! docker ps --format '{{.Names}}' | grep -Fxq "${proxy_container_name}"; then
        log "cookie proxy stopped during startup"
        docker logs --tail 120 "${proxy_container_name}" || true
        exit 1
    fi
    sleep 2
done
if [[ "${ready}" -ne 1 ]]; then
    log "Connect did not become ready"
    docker logs --tail 120 "${connect_container_name}" || true
    docker logs --tail 120 "${proxy_container_name}" || true
    exit 1
fi
if ! docker exec "${connect_container_name}" \
    /opt/rstudio-connect/bin/license-manager status >/dev/null 2>&1; then
    log "Connect license status is not active"
    exit 1
fi

log "bootstrapping a temporary Connect publisher API key"
export CONNECT_SERVER="${server_url}"
CONNECT_API_KEY="$("${repo_root}/.venv/bin/rsconnect" bootstrap \
    --server "${server_url}" --jwt-keypath "${bootstrap_key}" --raw)"
export CONNECT_API_KEY
if [[ -z "${CONNECT_API_KEY}" ]]; then
    log "Connect bootstrap did not return an API key"
    exit 1
fi

log "creating a temporary Connect viewer for browser-layer authentication"
connect_user_payload="$(jq -n \
    --arg username "${connect_browser_username}" \
    --arg password "${connect_browser_password}" \
    '{
        username: $username,
        first_name: "Connect",
        last_name: "Smoke",
        email: "connect-smoke@example.gov",
        user_role: "viewer",
        user_must_set_password: false,
        password: $password
    }')"
connect_user_status="$(curl --silent --show-error \
    -H "Authorization: Key ${CONNECT_API_KEY}" \
    -H 'Content-Type: application/json' \
    --data "${connect_user_payload}" \
    -o "${smoke_root}/connect-user-response.json" -w '%{http_code}' \
    "${server_url}/__api__/v1/users")"
if [[ "${connect_user_status}" != "200" ]]; then
    connect_user_error="$(jq -r '(.code // "unknown" | tostring) + ": " + (.error // "unknown error")' \
        "${smoke_root}/connect-user-response.json")"
    log "Connect rejected the temporary viewer with HTTP ${connect_user_status} (${connect_user_error})"
    exit 1
fi
if ! jq -e \
    --arg username "${connect_browser_username}" \
    '.username == $username and .locked == false and (.guid | type == "string")' \
    "${smoke_root}/connect-user-response.json" >/dev/null; then
    log "temporary Connect viewer was not created as an unlocked account"
    exit 1
fi
connect_browser_guid="$(jq -r '.guid' "${smoke_root}/connect-user-response.json")"

if ! jq -e '.confirmed == true' "${smoke_root}/connect-user-response.json" >/dev/null; then
    log "confirming the temporary Connect viewer through its one-time browser link"
    confirmation_json="$(curl --silent --show-error --fail \
        -H "Authorization: Key ${CONNECT_API_KEY}" \
        -X POST \
        "${server_url}/__api__/users/${connect_browser_guid}/confirm/resend")"
    confirmation_url="$(printf '%s' "${confirmation_json}" | jq -r '.url // empty')"
    confirmation_token="$(printf '%s' "${confirmation_url}" | \
        sed -n 's/.*[?&]utoken=\([^&]*\).*/\1/p')"
    confirmation_reset="$(printf '%s' "${confirmation_url}" | \
        sed -n 's/.*[?&]passwordreset=\([^&]*\).*/\1/p')"
    if [[ -z "${confirmation_token}" ]]; then
        log "Connect did not return a usable one-time confirmation link"
        exit 1
    fi
    invalidate_password=false
    if [[ "${confirmation_reset}" == "1" ]]; then
        invalidate_password=true
    fi
    confirmation_login_payload="$(jq -n \
        --arg username "${connect_browser_username}" \
        --arg password "${confirmation_token}" \
        --argjson invalidate_password "${invalidate_password}" \
        '{
            username: $username,
            password: $password,
            invalidatePassword: $invalidate_password
        }')"
    confirmation_login_status="$(curl --silent --show-error \
        -c "${confirmation_cookie_jar}" \
        -H 'Content-Type: application/json' \
        --data "${confirmation_login_payload}" \
        -o "${smoke_root}/confirmation-login-response.json" -w '%{http_code}' \
        "${server_url}/__login__")"
    if [[ "${confirmation_login_status}" != "200" ]]; then
        log "Connect account confirmation returned HTTP ${confirmation_login_status}"
        exit 1
    fi
fi

confirmed_user_json="$(curl --silent --show-error --fail \
    -H "Authorization: Key ${CONNECT_API_KEY}" \
    "${server_url}/__api__/v1/users/${connect_browser_guid}")"
if ! printf '%s' "${confirmed_user_json}" | jq -e \
    '.confirmed == true and .locked == false' >/dev/null; then
    log "temporary Connect viewer could not be confirmed"
    exit 1
fi

export CONNECT_COOKIE_BRIDGE_ENABLED=true

log "deploying the main app as FastAPI content"
(
    cd "${bundle_dir}"
    "${repo_root}/.venv/bin/rsconnect" deploy fastapi \
        --server "${server_url}" \
        --new \
        --title 'Access Registry Docker Smoke' \
        --entrypoint app.main:app \
        --no-verify \
        --requirements-file requirements.txt \
        --override-python-version 3.11.7 \
        --environment APP_ENV \
        --environment APP_NAME \
        --environment PUBLIC_BASE_URL \
        --environment DATABASE_URL \
        --environment JWT_SECRET \
        --environment SESSION_PEPPER \
        --environment CSRF_SECRET \
        --environment API_TOKEN_ENCRYPTION_KEYS \
        --environment API_TOKEN_ACTIVE_KEY_ID \
        --environment AUTHENTICATION_MODE \
        --environment COOKIE_SECURE \
        --environment COOKIE_PATH \
        --environment CONNECT_COOKIE_BRIDGE_ENABLED \
        --environment ALLOWED_EMAIL_DOMAINS \
        --environment RATE_LIMIT_ENABLED \
        --environment EMAIL_BACKEND \
        --environment ACCESS_REGISTRY_DEV_TRACE \
        --exclude='.env' \
        --exclude='.venv' \
        --exclude='**/__pycache__/*' \
        --exclude='tests' \
        --exclude='demo-app' \
        ./ \
        deployment/connect-smoke.db
)

content_json="$(curl --silent --show-error --fail \
    -H "Authorization: Key ${CONNECT_API_KEY}" \
    "${server_url}/__api__/v1/content")"
content_guid="$(printf '%s' "${content_json}" | jq -r \
    '[.[] | select(.title == "Access Registry Docker Smoke")] | last | .guid // empty')"
if [[ -z "${content_guid}" ]]; then
    log "deployed content could not be found through the Connect API"
    exit 1
fi
content_url="${server_url}/content/${content_guid}"

log "making only the disposable content reachable to logged-in Connect users"
access_status="$(curl --silent --show-error \
    -H "Authorization: Key ${CONNECT_API_KEY}" \
    -H 'Content-Type: application/json' \
    -X PATCH --data '{"access_type":"logged_in"}' \
    -o "${smoke_root}/access-response.json" -w '%{http_code}' \
    "${server_url}/__api__/v1/content/${content_guid}")"
if [[ "${access_status}" != "200" ]]; then
    log "Connect rejected temporary logged-in access with HTTP ${access_status}"
    exit 1
fi

log "signing the disposable browser into Connect"
connect_login_payload="$(jq -n \
    --arg username "${connect_browser_username}" \
    --arg password "${connect_browser_password}" \
    '{username: $username, password: $password, invalidatePassword: false}')"
connect_login_status="$(curl --silent --show-error \
    -c "${cookie_jar}" \
    -H 'Content-Type: application/json' \
    --data "${connect_login_payload}" \
    -o "${smoke_root}/connect-login-response.json" -w '%{http_code}' \
    "${server_url}/__login__")"
if [[ "${connect_login_status}" != "200" ]]; then
    log "temporary Connect browser login returned HTTP ${connect_login_status}"
    exit 1
fi
if ! awk '($0 !~ /^#/ || $0 ~ /^#HttpOnly_/) && $6 == "rsconnect" { found = 1 } END { exit !found }' \
    "${cookie_jar}"; then
    log "temporary Connect browser login did not establish a session cookie"
    exit 1
fi

log "checking health and mount-aware login through the Connect proxy"
health_json="$(curl --silent --show-error --fail \
    -b "${cookie_jar}" -c "${cookie_jar}" "${content_url}/health")"
printf '%s' "${health_json}" | jq -e '.status == "ok"' >/dev/null

curl --silent --show-error --fail \
    -b "${cookie_jar}" -c "${cookie_jar}" -D "${login_headers}" \
    "${content_url}/login" -o "${login_page}"
preauth_csrf="$(sed -n 's/.*name="preauth_csrf_token" value="\([^"]*\)".*/\1/p' \
    "${login_page}" | head -n 1)"
if [[ -z "${preauth_csrf}" ]]; then
    log "login page did not contain a pre-authentication CSRF token"
    exit 1
fi
if ! grep -Eqi "^set-cookie: access_registry_login_csrf=.*Path=/content/${content_guid}.*SameSite=lax" \
    "${login_headers}"; then
    log "login cookie did not use the expected Connect content path and SameSite policy"
    exit 1
fi
if ! awk -v expected_path="/content/${content_guid}" \
    '($0 !~ /^#/ || $0 ~ /^#HttpOnly_/) && \
        ($3 == expected_path || $3 == expected_path "/") && \
        $6 == "access_registry_login_csrf" { found = 1 } END { exit !found }' \
    "${cookie_jar}"; then
    log "curl did not retain the application's mount-scoped login cookie"
    awk '($0 !~ /^#/ || $0 ~ /^#HttpOnly_/) && $6 ~ /^access_registry_/ {
        printf "[connect-smoke] observed cookie metadata name=%s path=%s secure=%s\n", $6, $3, $4
    }' "${cookie_jar}" || true
    exit 1
fi

login_status="$(curl --silent --show-error \
    -b "${cookie_jar}" -c "${cookie_jar}" -D "${login_headers}" \
    --data-urlencode 'email=admin@example.gov' \
    --data-urlencode "password=${smoke_admin_password}" \
    --data-urlencode "preauth_csrf_token=${preauth_csrf}" \
    --data-urlencode 'next=/profile' \
    -o "${login_response}" -w '%{http_code}' \
    "${content_url}/login")"
if [[ "${login_status}" != "303" ]]; then
    log "app login returned HTTP ${login_status}, expected 303"
    show_app_diagnostics
    exit 1
fi
if ! grep -Eqi "^location: /content/${content_guid}/profile" "${login_headers}"; then
    log "login redirect did not remain under the Connect content path"
    exit 1
fi
for cookie_name in access_registry_access access_registry_refresh; do
    if ! grep -Eqi "^set-cookie: ${cookie_name}=.*Path=/content/${content_guid}.*SameSite=lax" \
        "${login_headers}"; then
        log "${cookie_name} did not use the expected Connect content path"
        exit 1
    fi
done

profile_status="$(curl --silent --show-error \
    -b "${cookie_jar}" -c "${cookie_jar}" \
    -o "${profile_page}" -w '%{http_code}' "${content_url}/profile")"
if [[ "${profile_status}" != "200" ]] || ! grep -q 'Your profile' "${profile_page}"; then
    log "authenticated profile smoke check failed"
    show_app_diagnostics
    exit 1
fi

log "confirming safe application diagnostics reached Connect content logs"
diagnostics_ready=0
for _ in {1..30}; do
    if docker exec "${connect_container_name}" /bin/bash -lc \
        'grep -R -q "auth.access.accepted" /data/jobs 2>/dev/null' >/dev/null 2>&1 && \
       docker exec "${connect_container_name}" /bin/bash -lc \
        'grep -R -q "csrf.preauth.accepted" /data/jobs 2>/dev/null' >/dev/null 2>&1 && \
       docker exec "${connect_container_name}" /bin/bash -lc \
        'grep -R -q "cookie.bridge.accepted" /data/jobs 2>/dev/null' >/dev/null 2>&1; then
        diagnostics_ready=1
        break
    fi
    sleep 1
done
if [[ "${diagnostics_ready}" -ne 1 ]]; then
    log "expected safe diagnostic events were not found in Connect content logs"
    show_app_diagnostics
    exit 1
fi

log "PASS: proxied deployment, health, CSRF cookie, app login, session cookies, profile, and logs"
