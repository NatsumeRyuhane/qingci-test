#!/usr/bin/env sh
# 从 uv.lock 导出 requirements.txt 和 requirements-dev.txt，供不用 uv 的备用路线（venv + pip）使用。
# 改了依赖之后运行本脚本，并把导出的文件一起提交。
#
#   scripts/export-requirements.sh          重新导出
#   scripts/export-requirements.sh --check  只检查导出的文件是否与 uv.lock 一致（CI 使用）
set -eu
cd "$(dirname "$0")/.."

export_to() {
  # $1：输出文件；其余参数传给 uv export
  out="$1"
  shift
  uv export --quiet --frozen --format requirements-txt --no-emit-project "$@" -o "$out"
}

if [ "${1:-}" = "--check" ]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  export_to "$tmp/requirements.txt" --no-dev
  export_to "$tmp/requirements-dev.txt"
  status=0
  for f in requirements.txt requirements-dev.txt; do
    # 首行注释里含有输出路径，比较时跳过注释行
    grep -v '^#' "$tmp/$f" >"$tmp/$f.expected"
    grep -v '^#' "$f" >"$tmp/$f.actual" 2>/dev/null || true
    if ! cmp -s "$tmp/$f.expected" "$tmp/$f.actual"; then
      echo "$f 与 uv.lock 不一致，请运行 scripts/export-requirements.sh 并提交结果。" >&2
      status=1
    fi
  done
  exit "$status"
fi

export_to requirements.txt --no-dev
export_to requirements-dev.txt
echo "已导出 requirements.txt 和 requirements-dev.txt"
