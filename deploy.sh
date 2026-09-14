#!/bin/bash
# Deploy the tracked files to the production box and restart the service.
#
#   ./deploy.sh            # rsync + fix perms + restart
#   ./deploy.sh --no-restart
#
# Why the chmod/chown step: macOS rsync (2.6.9) has no --chown, and with -a
# it also copies the local directory modes onto the server — a 700 project
# folder on the Mac turns /opt/rustmetrics/static into 700 owned by the Mac
# uid, and the service user can no longer read it (site answers 502/404).
set -euo pipefail
HOST="${RM_DEPLOY_HOST:-rustmetrics}"
DEST="${RM_DEPLOY_DIR:-/opt/rustmetrics}"
cd "$(dirname "$0")"
LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
git ls-files | grep -v '^assets/' > "$LIST"          # Blender sources stay local
rsync -a --checksum --files-from="$LIST" ./ "$HOST:$DEST/"
ssh "$HOST" "cd '$DEST' && chown -R rustmetrics:rustmetrics . \
  && find . -type d -not -path './.venv/*' -exec chmod 755 {} + \
  && find ./static -type f -exec chmod 644 {} + && chmod 644 *.py && chmod 600 .env"
if [[ "${1:-}" != "--no-restart" ]]; then
  ssh "$HOST" "systemctl restart rustmetrics && sleep 4 && systemctl is-active rustmetrics"
fi
curl -s -o /dev/null -w "live /app %{http_code}\n" https://rustmetrics.eu/app
