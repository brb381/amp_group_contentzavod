#!/usr/bin/env bash
set -euo pipefail

backend_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${1:-$backend_dir/.env}"
release_file="${2:-}"

if [[ -z "$release_file" ]]; then
  echo "Usage: $0 ENV_FILE RELEASE_ENV" >&2
  exit 2
fi

cd "$backend_dir"
mkdir -p ops/releases
exec 9>ops/releases/deploy.lock
flock -n 9 || { echo "Another deployment is already running." >&2; exit 1; }

python3 ops/validate_production_env.py --env-file "$env_file" --env-file "$release_file"
compose=(
  docker compose
  --env-file "$env_file"
  --env-file "$release_file"
  -f docker-compose.yml
  -f docker-compose.production.yml
)

"${compose[@]}" config --quiet
"${compose[@]}" pull
"${compose[@]}" up -d --no-build --wait
"${compose[@]}" exec -T web wget -q -O - http://127.0.0.1:8080/health/ready >/dev/null

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -m 600 "$release_file" "ops/releases/$stamp.env"
install -m 600 "$release_file" ops/releases/current.env
echo "Deployment $stamp completed."
