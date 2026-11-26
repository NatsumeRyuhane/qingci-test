#!/usr/bin/env sh
# 运维脚本 manage.py 的启动器，参数原样传给 manage.py（见 deploy/README.md）。
#
# - 装了 uv：用 `uv run --script` 运行，依赖取自 manage.py 中的 PEP 723 元数据。
# - 没装 uv：在 deploy/.venv 中用标准库 venv 建虚拟环境，用 pip 安装 deploy/requirements.txt。
#   requirements.txt 变化后会自动重新安装。不要求安装 uv。
set -eu

here="$(cd "$(dirname "$0")" && pwd)"

if [ -z "${COPILOT_NO_UV:-}" ] && command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --script "$here/manage.py" "$@"
fi

python="${PYTHON:-python3}"
if ! command -v "$python" >/dev/null 2>&1; then
  echo "找不到 $python。请安装 Python 3.9 或更新版本，或用 PYTHON=<路径> 指定。" >&2
  exit 1
fi

venv="$here/.venv"
if [ ! -x "$venv/bin/python" ]; then
  echo "==> 创建虚拟环境 $venv" >&2
  if ! "$python" -m venv "$venv"; then
    rm -rf "$venv"
    echo "创建虚拟环境失败。Debian/Ubuntu 上需要先安装：sudo apt install python3-venv" >&2
    exit 1
  fi
fi

stamp="$venv/.requirements.cksum"
wanted="$(cksum <"$here/requirements.txt")"
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$wanted" ]; then
  echo "==> 安装依赖（deploy/requirements.txt）" >&2
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$here/requirements.txt"
  printf '%s\n' "$wanted" >"$stamp"
fi

exec "$venv/bin/python" "$here/manage.py" "$@"
