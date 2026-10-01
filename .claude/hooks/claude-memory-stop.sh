#!/bin/bash
# Remind the agent to persist durable knowledge. This hook must never block work.
set +e
cat <<'EOF'
若本轮产生持久信息，请更新 .claude/memory/：新踩坑追加 learnings.md，新事实更新 wiki.md，新约定更新 memory.md。
EOF
exit 0

