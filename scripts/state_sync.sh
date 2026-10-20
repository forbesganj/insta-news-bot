#!/usr/bin/env bash
# Keeps the SQLite state (dedupe history, queue, posts, metrics, learned weights) on a separate
# "state" branch so it never clutters your code history.
#   bash scripts/state_sync.sh pull   -> checks the branch out into ./state (creates it on first run)
#   bash scripts/state_sync.sh push   -> commits and pushes any changes
set -euo pipefail
STATE_DIR="${STATE_DIR:-state}"
BRANCH="${STATE_BRANCH:-state}"
cmd="${1:?usage: state_sync.sh pull|push}"

case "$cmd" in
  pull)
    git worktree prune
    rm -rf "$STATE_DIR"
    if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      git fetch --depth 1 origin "$BRANCH"
      git worktree add -B "$BRANCH" "$STATE_DIR" FETCH_HEAD
    else
      echo "state branch not found; creating an empty one"
      git worktree add --detach "$STATE_DIR"
      git -C "$STATE_DIR" checkout --orphan "$BRANCH"
      git -C "$STATE_DIR" rm -rf . >/dev/null 2>&1 || true
    fi
    ;;
  push)
    cd "$STATE_DIR"
    git config user.name "newsroom-bot"
    git config user.email "newsroom-bot@users.noreply.github.com"
    git add -A
    if git diff --cached --quiet; then
      echo "no state changes"
      exit 0
    fi
    git commit -q -m "state: $(date -u +%Y-%m-%dT%H:%MZ)"
    git push origin "HEAD:refs/heads/$BRANCH"
    ;;
  *)
    echo "unknown command: $cmd" >&2
    exit 1
    ;;
esac
