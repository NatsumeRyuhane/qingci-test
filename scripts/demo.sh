#!/usr/bin/env sh
# 本地演示（#8）：每次启动都把演示数据库重置为初始状态，再启动后端和前端。
#
#   scripts/demo.sh           重置并启动；Ctrl-C 同时停止前后端，下次启动重新重置
#   scripts/demo.sh --reset   只重置，不启动
#
# 初始状态：已发布的知识库（knowledge/ 与模板审核结果）+ 从 dev 中挑选的 3 个样本案件，
# 没有草稿、反馈和发件箱。演示中投递的来信、生成的草稿和发出的回复都会在下次启动时清空。
#
# 只删除演示专用的数据库文件（DEMO_DB），不会碰 .env 中 DATABASE_URL 指向的数据库。
# 前提：已经按 README 运行过 copilot ingest、split、kb build、kb annotate；.env 中配置了
# LLM 端点和 key。两条工具链都能用：有 uv 时用 `uv run`，否则要求已激活装好后端的 venv；
# 有 pnpm 时直接用，否则用 `npx pnpm@<package.json 中固定的版本>`。
set -eu
cd "$(dirname "$0")/.."

DEMO_DB="${DEMO_DB:-data/processed/demo-workbench.db}"
export DATABASE_URL="sqlite:///$DEMO_DB"

# 演示配置，运行前设置同名环境变量即可覆盖（会覆盖 .env 中的值）。
# D-017：起草流水线全链路用 DeepSeek v4.1 Flash，自动发送阈值 0.92，后台起草用 flex。
# D-018：来信后自动起草，可以直接发送的草稿由系统自动发送（只进模拟发件箱）。
export LLM_MODEL_ANALYZE="${LLM_MODEL_ANALYZE:-deepseek/deepseek-v4.1-flash}"
export LLM_MODEL_DRAFT="${LLM_MODEL_DRAFT:-deepseek/deepseek-v4.1-flash}"
export LLM_MODEL_BACKTRANSLATE="${LLM_MODEL_BACKTRANSLATE:-deepseek/deepseek-v4.1-flash}"
export LLM_JSON_MODE="${LLM_JSON_MODE:-json_object}"
export LLM_MAX_RETRIES="${LLM_MAX_RETRIES:-3}"
export LLM_SERVICE_TIER="${LLM_SERVICE_TIER:-default}"
export DRAFT_AUTO_SEND_MIN_CONFIDENCE="${DRAFT_AUTO_SEND_MIN_CONFIDENCE:-0.92}"
export WORKBENCH_DRAFTER="${WORKBENCH_DRAFTER:-pipeline}"
export WORKBENCH_AUTO_DRAFT="${WORKBENCH_AUTO_DRAFT:-true}"
export WORKBENCH_AUTO_SEND="${WORKBENCH_AUTO_SEND:-true}"
export WORKBENCH_BACKGROUND_SERVICE_TIER="${WORKBENCH_BACKGROUND_SERVICE_TIER:-flex}"
DEMO_SEED_COUNT="${DEMO_SEED_COUNT:-3}"
BACKEND_PORT=8000  # 与 frontend/vite.config.ts 中的代理目标一致
FRONTEND_PORT="${DEMO_FRONTEND_PORT:-5173}"

if command -v uv >/dev/null 2>&1; then
  run_copilot() { uv run --quiet copilot "$@"; }
  exec_copilot() { exec uv run --quiet copilot "$@"; }
else
  command -v copilot >/dev/null 2>&1 || {
    echo "找不到 uv，也找不到 copilot 命令：请先激活装好后端的 venv（见 README）。" >&2
    exit 1
  }
  run_copilot() { copilot "$@"; }
  exec_copilot() { exec copilot "$@"; }
fi

if command -v pnpm >/dev/null 2>&1; then
  pnpm_cmd="pnpm"
else
  version="$(node -p "require('./frontend/package.json').packageManager.split('@')[1]")"
  pnpm_cmd="npx --yes pnpm@$version"
fi

for required in data/interim/cases.jsonl data/processed/split.json data/processed/kb/templates.jsonl; do
  if [ ! -e "$required" ]; then
    echo "缺少 $required：先按 README 运行 copilot ingest、split、kb build、kb annotate。" >&2
    exit 1
  fi
done

echo "== 重置演示数据库：$DEMO_DB"
rm -f "$DEMO_DB" "$DEMO_DB-wal" "$DEMO_DB-shm"
run_copilot db upgrade
run_copilot kb publish >/dev/null
run_copilot seed --count "$DEMO_SEED_COUNT"

if [ "${1:-}" = "--reset" ]; then
  exit 0
fi

echo "== 启动后端 http://127.0.0.1:$BACKEND_PORT 和前端 http://localhost:$FRONTEND_PORT"
echo "   工作台 http://localhost:$FRONTEND_PORT/#inbox · 模拟邮箱 #sim · 指标 #metrics"
# 在子 shell 中 exec，$! 就是后端进程本身，退出时能可靠地停止它
(exec_copilot serve --port "$BACKEND_PORT") &
backend=$!
# 退出时一并停止后端（uv 会把信号转发给 copilot 进程）
trap 'kill "$backend" 2>/dev/null || true' EXIT INT TERM
$pnpm_cmd --dir frontend dev --port "$FRONTEND_PORT" --strictPort
