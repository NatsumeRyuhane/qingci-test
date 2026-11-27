#!/usr/bin/env sh
# 从后端的 OpenAPI 规范生成前端 API 类型（frontend/src/api/schema.d.ts）。
# 改了后端 API 之后运行本脚本，并把生成的文件一起提交。
#
#   scripts/gen-api-types.sh          重新生成
#   scripts/gen-api-types.sh --check  只检查生成的文件是否最新（CI 使用）
#
# 两条工具链路线都能用：有 uv 时用 `uv run`，否则要求已激活装好后端的 venv；
# 有 pnpm 时直接用，否则用 `npx pnpm@<package.json 中固定的版本>`。
set -eu
cd "$(dirname "$0")/.."

out=frontend/src/api/schema.d.ts
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if command -v uv >/dev/null 2>&1; then
  uv run --quiet copilot openapi >"$tmp/openapi.json"
else
  copilot openapi >"$tmp/openapi.json"
fi

if command -v pnpm >/dev/null 2>&1; then
  pnpm_cmd="pnpm"
else
  version="$(node -p "require('./frontend/package.json').packageManager.split('@')[1]")"
  pnpm_cmd="npx --yes pnpm@$version"
fi

$pnpm_cmd --dir frontend exec openapi-typescript "$tmp/openapi.json" -o "$tmp/schema.d.ts" >/dev/null

if [ "${1:-}" = "--check" ]; then
  if ! cmp -s "$tmp/schema.d.ts" "$out"; then
    echo "$out 不是最新的，请运行 scripts/gen-api-types.sh 并提交结果。" >&2
    exit 1
  fi
  exit 0
fi

mkdir -p "$(dirname "$out")"
cp "$tmp/schema.d.ts" "$out"
echo "已生成 $out"
