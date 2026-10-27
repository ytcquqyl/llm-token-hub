#!/usr/bin/env bash
# ============================================================
# LLM Token Hub · 发布到 GitHub 并自动构建 GHCR 镜像
# 在你自己的电脑 / NAS / 任意已登录 gh 的机器上执行：
#   cd llm-token-hub && bash deploy/publish-github.sh
# 前置：安装 gh (https://cli.github.com) 并 gh auth login
#       或设置环境变量 GITHUB_TOKEN=<PAT: repo + workflow + write:packages>
# ============================================================
set -euo pipefail

REPO_NAME="${REPO_NAME:-llm-token-hub}"
OWNER="${OWNER:-ytcquqyl}"
VISIBILITY="${VISIBILITY:---public}"     # 想私有就改成 --private
BRANCH="${BRANCH:-main}"

say() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }

# ---------- 1. 检查环境 ----------
say "1/5 检查环境"
command -v git >/dev/null || { echo "需要 git"; exit 1; }
if ! command -v gh >/dev/null; then
  echo "未检测到 gh CLI。两种方式二选一："
  echo "  A. 安装后登录：https://cli.github.com  然后 gh auth login"
  echo "  B. 设置令牌：export GITHUB_TOKEN=<PAT: repo + workflow + write:packages>"
  exit 1
fi
gh auth status >/dev/null 2>&1 || { echo "gh 未登录，请先执行 gh auth login（或 export GITHUB_TOKEN=...）"; exit 1; }
echo "已认证：$(gh api user --jq .login 2>/dev/null || echo "$OWNER")"

# ---------- 2. 本地提交 ----------
say "2/5 本地提交"
git add -A
if ! git diff --cached --quiet; then
  git commit -qm "${COMMIT_MSG:-chore: 更新 LLM Token Hub}"
else
  echo "无新改动，跳过提交"
fi
git branch -M "$BRANCH"

# ---------- 3. 创建仓库并推送 ----------
say "3/5 创建仓库 ${OWNER}/${REPO_NAME} 并推送"
if gh repo view "${OWNER}/${REPO_NAME}" >/dev/null 2>&1; then
  echo "仓库已存在，直接推送"
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://github.com/${OWNER}/${REPO_NAME}.git"
else
  gh repo create "${REPO_NAME}" $VISIBILITY --source=. --remote=origin --push
fi
git push -u origin "$BRANCH"

# ---------- 4. 等待 Actions 构建 ----------
say "4/5 等待 GitHub Actions 构建镜像"
sleep 8
for i in $(seq 1 40); do
  STATUS=$(gh api "repos/${OWNER}/${REPO_NAME}/actions/runs?per_page=1" --jq '.workflow_runs[0].status' 2>/dev/null || echo "")
  CONC=$(gh api "repos/${OWNER}/${REPO_NAME}/actions/runs?per_page=1" --jq '.workflow_runs[0].conclusion' 2>/dev/null || echo "")
  if [[ "$STATUS" == "completed" ]]; then
    echo "构建结束：${CONC}"
    [[ "$CONC" == "success" ]] || { echo "构建失败，查看：https://github.com/${OWNER}/${REPO_NAME}/actions"; exit 1; }
    break
  fi
  echo "  构建中…(${STATUS:-排队}) ${i}/40"
  sleep 15
done

# ---------- 5. 输出镜像地址与 NAS 拉取方式 ----------
say "5/5 完成"
IMAGE="ghcr.io/${OWNER}/${REPO_NAME}:latest"
cat <<EOF

仓库：    https://github.com/${OWNER}/${REPO_NAME}
镜像：    ${IMAGE}
构建日志：https://github.com/${OWNER}/${REPO_NAME}/actions

NAS 上拉取部署（飞牛 SSH）：
  docker pull ${IMAGE}
  cd /vol1/1000/docker/llm-token-hub && docker compose up -d

若提示 no pull access（镜像包未公开）：
  打开 https://github.com/${OWNER}?tab=packages → llm-token-hub
  → Package settings → Change visibility → Public
  或在 NAS 上：echo <PAT> | docker login ghcr.io -u ${OWNER} --password-stdin
EOF
