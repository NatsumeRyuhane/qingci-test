# 部署指南

用 Docker Compose 在一台服务器上运行坐席助手，日常操作都通过运维脚本 `deploy/manage.sh` 完成（[#14](https://github.com/NatsumeRyuhane/qingci-test/issues/14)）。

> **安全提示：** MVP 的界面和 API **没有认证**（见 [#8](https://github.com/NatsumeRyuhane/qingci-test/issues/8)、[#13](https://github.com/NatsumeRyuhane/qingci-test/issues/13)）。在 #13 完成之前，**不要把服务暴露到公网**，只在内网或 VPN 后使用。默认只监听本机（`127.0.0.1`）。

## 架构

```
浏览器 ──► frontend（nginx，唯一对外的端口）──┬─ 静态文件（React 打包产物）
                                              └─ /v1 ──► backend（FastAPI，只在容器网络内）
                                                            │
                                         数据目录（宿主机，挂载到 /app/data）
                                         ├─ copilot.db          SQLite 数据库
                                         ├─ raw/ interim/ processed/   样本数据与派生数据
                                         └─ deploy/             备份与部署记录
```

- `backend` 只能运行 **1 个副本**：SQLite 同一时间只允许一个写者（D-010）。
- 样本数据和密钥都不在镜像中，运行时挂载数据目录、通过 `env_file` 注入 `.env`（D-002、D-005）。
- 前端与后端同源，不需要 CORS，与开发时的 Vite 代理行为一致。

| 文件 | 作用 |
|---|---|
| `compose.yaml` | 两个服务、健康检查、数据目录挂载 |
| `backend.Dockerfile`、`frontend.Dockerfile` | 镜像 |
| `nginx.conf` | 静态文件与 `/v1` 反向代理 |
| `manage.py`、`manage.sh` | 运维脚本及其启动器 |
| `requirements.txt` | `manage.py` 的依赖（不用 uv 时由 `manage.sh` 自动安装） |
| `.env.example` | 部署参数模板（端口、监听地址、数据目录） |

## 前提条件

- **Docker**，含 Compose v2（`docker compose version` 能运行）。当前用户需要能运行 `docker`，例如已加入 `docker` 组。
- **Python 3.9 或更新版本**，只用来运行运维脚本。不需要安装 uv：
  - 装了 [uv](https://docs.astral.sh/uv/) 时，`manage.sh` 用 `uv run` 运行；
  - 没装时，`manage.sh` 在 `deploy/.venv` 中用标准库 `venv` 建虚拟环境，再用 pip 安装依赖。Debian/Ubuntu 需要先 `sudo apt install python3-venv`。
- 能访问 Docker Hub、ghcr.io 和 npm、PyPI（构建镜像时下载基础镜像和依赖）。
- 一个 OpenAI 兼容的 LLM 端点，以及 embedding 端点。

## 首次部署

```sh
git clone <仓库地址> && cd qingci-test

# 1. 应用配置和密钥（LLM 端点等），说明见文件内注释
cp .env.example .env
vi .env

# 2. 可选：部署参数（端口、监听地址、数据目录）
cp deploy/.env.example deploy/.env
vi deploy/.env

# 3. 样本数据（向仓库所有者索取，见 data/README.md）
cp <路径>/脱敏数据2026.2.10.xlsx data/raw/

# 4. 部署
deploy/manage.sh deploy
```

`deploy` 依次执行：环境检查 → 构建镜像 → 迁移数据库 → 启动容器 → 等待健康检查通过。完成后在浏览器中打开脚本输出的地址（默认 `http://127.0.0.1:8080/`）。

在内网中使用时，在 `deploy/.env` 中设置 `COPILOT_BIND=0.0.0.0`（或服务器的内网 IP），也可以放在已有的反向代理之后，由它负责 TLS 和域名。

## 运维脚本

不带参数在终端中运行 `deploy/manage.sh` 会显示交互菜单。每个菜单项都有等价的子命令：

| 子命令 | 作用 |
|---|---|
| `check` | 检查 Docker 和 Compose v2；`.env` 是否存在、必填变量是否都已填写（只报告变量名，不输出值）；数据目录是否存在、属主是否合适；样本数据是否就位（只是提醒） |
| `deploy` | 首次部署（见上文） |
| `redeploy` | 先备份数据库，再构建、迁移、重建容器、等待健康检查。**基于当前检出的代码**，不会自动 `git pull` |
| `status` | 容器状态、`/v1/health` 的结果和当前部署的提交 |
| `logs [backend\|frontend] [-f] [--tail N]` | 查看日志 |
| `down` | 停止并删除容器，**保留数据** |
| `down --delete-data` | 同时删除数据库，需要输入 `delete` 确认；脚本中用 `--yes` 跳过确认。样本数据和备份不会删除 |

`deploy/manage.sh --help` 和 `deploy/manage.sh <子命令> --help` 查看全部参数。

## 更新

```sh
git pull                       # 或 git checkout <要部署的提交>
deploy/manage.sh redeploy
```

## 备份与恢复

`redeploy` 每次都会先用 SQLite 的在线备份接口备份数据库（服务运行中也能得到一致的副本），保存在 `<数据目录>/deploy/backups/copilot-<UTC 时间>.db`。旧备份不会自动清理，请按需删除。

手动备份：直接再运行一次 `redeploy`，或在服务停止时复制 `copilot.db`。

恢复到某个备份（`redeploy` 失败时脚本也会打印这些步骤）：

```sh
deploy/manage.sh down
cp data/deploy/backups/copilot-<时间>.db data/copilot.db
rm -f data/copilot.db-wal data/copilot.db-shm
git checkout <与备份对应的提交>      # 上次部署的提交记录在 data/deploy/state.json
deploy/manage.sh deploy
```

## 导入样本数据并构建知识库

一次性任务在一个临时的后端容器中运行，与服务共用数据目录和 `.env`：

```sh
export COPILOT_UID=$(id -u) COPILOT_GID=$(id -g)   # 以数据目录属主运行，见“常见问题”
docker compose -f deploy/compose.yaml run --rm backend copilot --help
docker compose -f deploy/compose.yaml run --rm backend copilot <子命令> [参数]
```

导入（[#4](https://github.com/NatsumeRyuhane/qingci-test/issues/4)）和构建知识库（[#5](https://github.com/NatsumeRyuhane/qingci-test/issues/5)）的子命令以 `copilot --help` 列出的为准。容器中的工作目录是 `/app`，数据目录挂载在 `/app/data`，所以 `.env` 中 `data/` 开头的相对路径（例如 `SAMPLE_DATA_PATH`）在容器中同样有效。

## 配置

- **应用配置**写在仓库根目录的 `.env`（模板 `.env.example`），通过 `env_file` 注入后端容器。`DATABASE_URL` 在容器中固定为数据目录下的 `copilot.db`，`.env` 中的值不生效。
- **部署参数**写在 `deploy/.env`（模板 `deploy/.env.example`），全部可选。进程环境变量优先于 `deploy/.env`：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `COPILOT_PORT` | `8080` | 对外端口 |
| `COPILOT_BIND` | `127.0.0.1` | 监听地址 |
| `COPILOT_DATA_DIR` | `../data` | 数据目录，相对于 `deploy/` |
| `COPILOT_UID`、`COPILOT_GID` | 数据目录的属主 | 后端容器的运行用户 |
| `COPILOT_NO_UV` | 空 | 设为任意值时，`manage.sh` 即使装了 uv 也走 venv + pip 路线（用于测试） |

## 常见问题

**`check` 提示“数据目录属于 root”。** 后端容器以数据目录属主的身份运行，这样容器写入的文件在宿主机上仍归你所有。把数据目录交给部署用户：`sudo chown -R $(id -u):$(id -g) data`。

**直接用 `docker compose` 命令时提示数据库无法写入。** 没有通过 `manage.sh` 运行时，容器默认以 UID 10001 运行。先 `export COPILOT_UID=$(id -u) COPILOT_GID=$(id -g)`，或把它们写进 `deploy/.env`。

**健康检查超时。** 先看 `deploy/manage.sh logs backend`。如果容器是健康的，但脚本连不上，检查 `COPILOT_BIND`、`COPILOT_PORT` 是否被占用，以及防火墙。

**端口已被占用。** 在 `deploy/.env` 中修改 `COPILOT_PORT`，再运行 `deploy/manage.sh redeploy`。

**能否运行多个后端副本？** 不能。SQLite 只允许一个写者，`compose.yaml` 用固定的容器名防止扩容。需要多实例时先迁移到 Postgres（D-010 中的迁移条件）。

**`python3 -m venv` 失败。** Debian/Ubuntu 需要 `sudo apt install python3-venv`。也可以安装 uv，`manage.sh` 会自动改用它。

## 开发与测试

```sh
# manage.py 的单元测试（mock 掉 Docker 调用，不需要 Docker），并检查依赖声明一致
uv run --no-project --with-requirements deploy/requirements.txt --with pytest pytest deploy/tests
```

`manage.py` 的依赖同时写在文件开头的 PEP 723 元数据和 `requirements.txt` 中，改动时两处一起改，测试会检查两者一致。`manage.py` 要能在服务器自带的 Python 3.9+ 上运行，ruff 对 `deploy/` 按 3.9 检查语法。

完整的部署流程由 CI 的 `deploy` job 验证（[AGENTS.md](../AGENTS.md) 第 8 节）。
