#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO_URL="${CLARITY_REPO_URL:-https://github.com/Valar05/clarity-game-engine.git}"
DEST="${CLARITY_SOURCE_DIR:-$HOME/src/clarity-game-engine}"
VENV="${CLARITY_VENV:-$HOME/.clarity-venv}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 2; }; }
need git
need python

mkdir -p "$(dirname "$DEST")"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" fetch --prune origin
  git -C "$DEST" checkout main
  git -C "$DEST" reset --hard origin/main
else
  git clone --branch main "$REPO_URL" "$DEST"
fi

python -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/pip" install -e "$DEST"

"$VENV/bin/clarity" doctor
"$VENV/bin/clarity" init
"$VENV/bin/clarity" verify

cat <<EOF
Clarity installed.
CLI: $VENV/bin/clarity
Source: $DEST
Runtime: ${CLARITY_HOME:-$HOME/.clarity}
EOF
