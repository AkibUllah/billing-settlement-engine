#!/usr/bin/env bash
# Codespaces start hook: bring up web + PostgreSQL, seed demo data and run one settlement.
set -euo pipefail
cd "$(dirname "$0")/.."

# Requests arrive through the Codespaces HTTPS proxy under a *.app.github.dev host.
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  HOST="${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
  export DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1,web,${HOST}"
  export DJANGO_CSRF_TRUSTED_ORIGINS="https://${HOST}"
fi

docker compose up -d --build

echo "waiting for the API on :8000 ..."
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/api/health/ >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# seed_demo is idempotent; it prints the admin login and the API token.
docker compose exec -T web python manage.py seed_demo | tee .demo-credentials.txt
docker compose exec -T web python manage.py run_settlement || true

cat <<'MSG'

Billing & Settlement Engine is up.
  Swagger UI:   forwarded port 8000, path /api/docs/   (Authorize with "Token <key>")
  Admin:        forwarded port 8000, path /admin/      (demo / demo12345)
  Credentials:  .demo-credentials.txt (API token on the last line)
MSG
