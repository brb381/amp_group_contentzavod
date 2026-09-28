#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 ENV_FILE PREVIOUS_RELEASE_ENV" >&2
  exit 2
fi

echo "Rollback changes application images only; database migrations are not downgraded." >&2
"$(dirname "${BASH_SOURCE[0]}")/deploy.sh" "$1" "$2"
