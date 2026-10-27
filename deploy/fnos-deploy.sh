#!/usr/bin/env bash
# ============================================================
# LLM Token Hub · 飞牛 NAS (fnOS) 一键部署脚本
# 用法（在飞牛 NAS 的 SSH 里执行，需 root/sudo）：
#   curl -fsSL https://raw.githubusercontent.com/ytcquqyl/llm-token-hub/main/deploy/fnos-deploy.sh \
#     | sudo bash -s -- --password '你的Web密码' --secret '随机长字符串' --port 8787
# 或先把整个项目拷到 NAS 上：
#   sudo bash deploy/fnos-deploy.sh --dir /vol1/1000/docker/llm-token-hub --password 'xxx' --secret 'yyy'
# ============================================================
set -euo pipefail

# ---------- 默认参数（可命令行覆盖） ----------
DIR="${DIR:-/vol1/1000/docker/llm-token-hub}"
PORT="${PORT:-8787}"
PASSWORD="${PASSWORD:-}"
SECRET="${SECRET:-}"
IMAGE="${IMAGE:-ghcr.io/ytcquqyl/llm-token-hub:latest}"
BUILD_LOCAL="${BUILD_LOCAL:-auto}"   # auto: 拉不到镜像就本地构建
TZ_NAME="${TZ_NAME:-Asia/Shanghai}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --password) PASSWORD="$2"; shift 2;;
    --secret) SECRET="$2"; shift 2;;
    --image) IMAGE="$2"; shift 2;;
    --tz) TZ_NAME="$2"; shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "未知参数: $1"; exit 1;;
  esac
done

say() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }

# ---------- 1. 环境检查 ----------
say "1/6 检查环境"
[[ $EUID -ne 0 ]] && { echo "请用 root 或 sudo 执行"; exit 1; }
command -v docker >/dev/null || { echo "未检测到 docker，请先在飞牛应用中心安装「Docker / 容器管理」"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker 守护进程未运行，请在飞牛 UI 中启动 Docker 服务"; exit 1; }
echo "docker: $(docker --version)"

# ---------- 2. 收集必填配置 ----------
say "2/6 配置参数"
if [[ -z "$PASSWORD" ]]; then
  read -rsp "设置 Web 访问密码 (ADMIN_PASSWORD): " PASSWORD; echo
fi
if [[ -z "$SECRET" ]]; then
  SECRET="$(openssl rand -hex 24 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "已自动生成加密主密钥 APP_SECRET（请务必保存！）"
fi
[[ -z "$PASSWORD" ]] && { echo "访问密码不能为空"; exit 1; }

# ---------- 3. 准备目录 ----------
say "3/6 准备目录 $DIR"
mkdir -p "$DIR/data"
# 若脚本与项目一起拷贝到了 NAS，把 app/static 同步过去
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$SRC/app/main.py" && "$SRC" != "$DIR" ]]; then
  mkdir -p "$DIR/app" "$DIR/static"
  cp -f "$SRC/app/main.py" "$DIR/app/"
  cp -f "$SRC/static/index.html" "$DIR/static/"
  cp -f "$SRC/requirements.txt" "$DIR/" 2>/dev/null || true
  echo "已同步源码到 $DIR"
fi
chmod 700 "$DIR/data"

# ---------- 4. 生成 .env 与 docker-compose.yml ----------
say "4/6 生成配置文件"
cat > "$DIR/.env" <<EOF
ADMIN_PASSWORD=$PASSWORD
APP_SECRET=$SECRET
TZ=$TZ_NAME
HUB_PORT=$PORT
IMAGE=$IMAGE
DB_PATH=/app/data/data.db
EOF
chmod 600 "$DIR/.env"

cat > "$DIR/docker-compose.yml" <<EOF
services:
  llm-token-hub:
    image: \${IMAGE:-ghcr.io/ytcquqyl/llm-token-hub:latest}
    container_name: llm-token-hub
    restart: unless-stopped
    ports:
      - "\${HUB_PORT:-8787}:8787"
    environment:
      ADMIN_PASSWORD: "\${ADMIN_PASSWORD}"
      APP_SECRET: "\${APP_SECRET}"
      DB_PATH: "\${DB_PATH:-/app/data/data.db}"
      TZ: "\${TZ:-Asia/Shanghai}"
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/healthz')"]
      interval: 30s
      timeout: 5s
      retries: 3
EOF

# ---------- 5. 拉取镜像或本地构建 ----------
say "5/6 获取镜像"
cd "$DIR"
if [[ "$BUILD_LOCAL" == "always" ]]; then
  PULL_OK=1
elif docker compose pull 2>/dev/null || docker pull "$IMAGE" >/dev/null 2>&1; then
  PULL_OK=0
else
  PULL_OK=1
fi

if [[ $PULL_OK -ne 0 ]]; then
  echo "镜像拉取失败（无外网/GHCR 不可达），改为本地构建"
  [[ -f "$DIR/app/main.py" ]] || { echo "缺少源码，无法本地构建：请把项目目录完整拷到 NAS"; exit 1; }
  sed -i "s|^    image: .*|    build: .|" "$DIR/docker-compose.yml"
  docker compose build --no-cache
fi

docker compose up -d

# ---------- 6. 验收 ----------
say "6/6 启动验收"
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    echo "健康检查通过"; break
  fi
  sleep 2
  [[ $i -eq 20 ]] && { echo "启动超时，执行：docker compose logs -f"; exit 1; }
done

IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<NAS内网IP>')"
cat <<EOF

================ 部署完成 ================
访问地址（手机同局域网打开）：
    http://${IP}:${PORT}
登录密码：你在上面设置的 ADMIN_PASSWORD

重要：APP_SECRET = ${SECRET}
  · 这是 API Key 的加密主密钥，改掉它旧 Key 将无法解密
  · 请单独保存（密码管理器），不要只存在 NAS 上

常用命令（在 ${DIR} 目录执行）：
    查看状态：docker compose ps
    查看日志：docker compose logs -f
    重启：    docker compose restart
    升级：    docker compose pull && docker compose up -d
    备份：    cp ${DIR}/data/data.db ~/llm-token-hub-$(date +%F).db
==========================================
EOF
