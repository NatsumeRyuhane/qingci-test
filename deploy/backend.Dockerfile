# 后端镜像。构建上下文是仓库根目录（见 compose.yaml），排除规则见根目录的 .dockerignore。
# 镜像中不含 data/ 和 .env：样本数据和密钥在运行时挂载或注入（D-002、D-005）。

# uv 版本与 CI 的 UV_VERSION 一致（.github/workflows/ci.yml）。
FROM ghcr.io/astral-sh/uv:0.12.14 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先只装依赖，代码改动时可以复用这一层缓存。
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
# 人工编写的运营知识（不含客户原文）。`copilot kb publish` 按相对路径读取 knowledge/。
COPY knowledge ./knowledge
RUN uv sync --frozen --no-dev --no-editable

# 非 root 用户。compose 会用 COPILOT_UID/COPILOT_GID 覆盖成数据目录的属主，
# 这样容器能写挂载进来的 data/，宿主机上的文件属主也不会被改掉（见 deploy/README.md）。
RUN groupadd --system --gid 10001 copilot \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home copilot \
    && mkdir -p /app/data \
    && chown copilot:copilot /app/data
USER copilot

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["copilot", "serve", "--host", "0.0.0.0", "--port", "8000"]
