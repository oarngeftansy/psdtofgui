#!/bin/bash
# Inject repository memory at session start. This hook must never block work.
set +e
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
MEM="$ROOT/.claude/memory"
emit() {
  [ -f "$1" ] || return 0
  local body
  body="$(grep -vE '^[[:space:]]*(>|#|$)' "$1")"
  [ -z "${body//[[:space:]]/}" ] && return 0
  echo "## $2"
  echo '```markdown'
  cat "$1"
  echo '```'
  echo
}
emit "$MEM/memory.md" "memory.md —— 偏好与约定"
emit "$MEM/wiki.md" "wiki.md —— 客观事实"
emit "$MEM/learnings.md" "learnings.md —— 过程经验"
exit 0

