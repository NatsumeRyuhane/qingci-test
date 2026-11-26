# 前端镜像：构建阶段用 Node 打包，运行阶段用 nginx 提供静态文件并把 /v1 代理到后端。
# 构建上下文是仓库根目录（见 compose.yaml）。

# 主版本与 frontend/.nvmrc 一致。
FROM node:22-slim AS build

WORKDIR /app/frontend

# pnpm 版本取自 package.json 的 packageManager 字段，与开发和 CI 一致，不在这里重复写版本号。
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install --global --no-fund --no-audit "$(node -p "require('./package.json').packageManager")" \
    && pnpm install --frozen-lockfile

COPY frontend ./
RUN pnpm build

# 非 root 的官方 nginx 镜像，监听 8080。
FROM nginxinc/nginx-unprivileged:stable-alpine

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html

EXPOSE 8080
