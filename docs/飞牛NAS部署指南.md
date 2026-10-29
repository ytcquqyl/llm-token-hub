# LLM Token Hub · 飞牛 NAS（fnOS）部署指南

面向「在自己 NAS 上自托管」的完整安装说明，也可直接交给另一台机器上的 WorkBuddy / 运维同学照着执行。
全程只需要：一台开着 SSH 的飞牛 NAS、Docker 服务、局域网内网的手机。

---

## 0. 部署前 3 分钟确认清单

| 项目 | 要求 | 怎么确认 |
|------|------|----------|
| fnOS 版本 | 已支持 Docker / 容器管理 | 飞牛应用中心搜索「Docker」 |
| Docker 服务 | 已启动 | 飞牛 UI：Docker 图标正常；或 SSH 执行 `docker info` |
| SSH | 已开启 | 飞牛「系统设置 → 开发者/远程 → 开启 SSH」（默认 22 端口） |
| 存储池路径 | 记住一个目录，如 `/vol1/1000/docker` | 飞牛文件管理里看存储池名，或 SSH `df -h` |
| 内网 IP | 例如 `192.168.4.x` | SSH 执行 `hostname -I`，或在爱快路由「终端管理」里看 |
| 端口 | 8787（可改） | 确认未被占用：`ss -lntp | grep 8787` |

> 建议：在爱快路由里给 NAS 按 MAC 做**静态 IP 绑定**，否则重启后 IP 变了，手机收藏的链接会失效。

---

## 1. 三种部署方式（任选其一）

### 方式 A：SSH 一键脚本（推荐）

在飞牛 NAS 的 SSH 会话里执行（需 root 或 sudo）：

```bash
# 1) 把项目放到 NAS（本地机器执行，或用飞牛文件管理上传 zip 后解压）
scp -r ./llm-token-hub root@192.168.4.x:/vol1/1000/docker/

# 2) SSH 登录 NAS
ssh root@192.168.4.x

# 3) 执行部署脚本
cd /vol1/1000/docker/llm-token-hub
sudo bash deploy/fnos-deploy.sh --password '你的Web访问密码' --secret '一串随机长字符串'
```

脚本会依次完成：环境检查 → 目录与权限 → 生成 `.env` + `docker-compose.yml` → 拉镜像（拉不到自动转本地构建）→ 启动 → 健康检查 → 打印访问地址。

常用参数：

```
--dir   项目目录，默认 /vol1/1000/docker/llm-token-hub
--port  对外端口，默认 8787
--password  Web 登录密码（ADMIN_PASSWORD）
--secret    API Key 加密主密钥（APP_SECRET，脚本不传则自动生成）
--image     镜像地址，默认 ghcr.io/ytcquqyl/llm-token-hub:latest
--tz        时区，默认 Asia/Shanghai
```

### 方式 B：飞牛 Docker UI（容器 / 项目管理）

1. 飞牛桌面打开 **Docker（容器管理）** → **项目（Compose）** → **创建项目**；
2. 项目名填 `llm-token-hub`，路径选择 `/vol1/1000/docker/llm-token-hub`；
3. Compose 内容粘贴下面这段（把两处密码替换掉）：

```yaml
services:
  llm-token-hub:
    image: ghcr.io/ytcquqyl/llm-token-hub:latest
    container_name: llm-token-hub
    restart: unless-stopped
    ports:
      - "8787:8787"
    environment:
      ADMIN_PASSWORD: "改成你的强密码"
      APP_SECRET: "改成随机长字符串"
      DB_PATH: "/app/data/data.db"
      TZ: "Asia/Shanghai"
    volumes:
      - ./data:/app/data
```

4. 点「构建/启动」，等待状态变为 **running**；
5. 手机浏览器打开 `http://<NAS内网IP>:8787`，输入 `ADMIN_PASSWORD` 登录。

> 如果飞牛 UI 拉不到 GHCR 镜像（国内网络常见），改用**方式 C**。

### 镜像从哪来（GHCR 自动构建）

仓库推送到 GitHub 后，`.github/workflows/docker-image.yml` 会自动构建 `linux/amd64 + linux/arm64` 镜像并发布到：

```
ghcr.io/ytcquqyl/llm-token-hub:latest
```

所以方式 A / B 默认就是**直接拉 GHCR 镜像**，NAS 上无需编译。手动触发一次发布：`bash deploy/publish-github.sh`。

若 `docker pull` 报 `no pull access`：GitHub 个人页 → Packages → `llm-token-hub` → Package settings → Change visibility → **Public**；
或 NAS 上 `echo <PAT> | docker login ghcr.io -u ytcquqyl --password-stdin` 后重拉。

### 方式 C：无外网 / 拉不到镜像 → 本地构建

```bash
cd /vol1/1000/docker/llm-token-hub
# 把 compose 里的 image: 换成 build: .
sed -i 's|^    image: .*|    build: .|' docker-compose.yml
docker compose up -d --build
```

构建只依赖 `python:3.12-slim` 基础镜像和 PyPI 依赖；若 PyPI 慢，可先 `pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`（在 NAS 上全局配置，或在 Dockerfile 里加 `-i` 参数）。

---

## 2. 首次使用（手机端）

1. 打开 `http://<NAS内网IP>:8787` → 输入登录密码；
2. 点右上角 **+ 添加** → 选平台 → 粘贴 API Key → 保存；
3. 列表页点 **复制 Key**，直接粘贴到你要配置的工具里；
4. 点顶部 **一键刷新全部额度**，DeepSeek / 智谱 / Moonshot / 硅基流动 会显示余额；
5. 需要新 Key 时点 **去创建/管理 Key** → 跳转官方控制台 → 新建后复制回来粘贴。
6. 顶栏 **设置** 里可随时修改**登录密码**与**加密密钥**并持久化，无需重建容器；改加密密钥会按新密钥自动重加密库内全部 Key 与 AI 密钥。
7. 顶部切到 **模型库** 标签页：点 **从平台导入模型** → 选一个已添加的账号 → 自动拉取该平台的 `/v1/models` 模型列表 → 勾选导入；每个模型可点 **编辑说明** 手写适用场景，或在 **设置 → AI 模型说明配置** 里填一个 OpenAI 兼容接口后，点 **AI 生成说明** 让 AI 自动写适用场景。

| 平台 | 余额查询 |
|------|----------|
| DeepSeek、智谱 GLM、Moonshot/Kimi、硅基流动 | ✅ 可查 |
| OpenAI、通义千问 DashScope | ❌ 无公开接口，UI 显示「该平台无公开余额接口」并提供控制台直达 |

---

## 3. 部署验收（跑一遍就心里有数）

```bash
cd /vol1/1000/docker/llm-token-hub
sudo bash deploy/verify.sh --port 8787 --password '你的Web密码'
```

脚本检查 9 项：容器状态 / 健康检查 / 未登录 401 / 登录成功 / 写入并解密读回一致 / **数据库文件不含明文 Key** / 修改登录密码（旧密码失效·新密码可用）/ 修改加密密钥（重加密后仍能解密）/ 清理测试数据。
全绿即部署成功。

---

## 4. 安全加固（家用也建议做）

1. **`APP_SECRET`（加密密钥）备份**：首次部署后会落入数据卷 `data/data.db` 的 `settings` 表（也可在网页「设置」里随时修改，修改会用新密钥重加密全部 Key）。迁移/恢复备份时必须连同 `settings` 中的密钥一起，单独记下的旧 `APP_SECRET` 变量无法解密新密钥加密的数据；建议连 `data/` 目录一起加密备份。
2. **不要直接暴露到公网**：仅内网访问；若需远程，用 Tailscale / WireGuard 组虚拟局域网，或飞牛自带的 fnConnect + 强密码。
3. **反向代理加 HTTPS**（可选）：Caddy/Nginx 反代 8787，配证书 + Basic Auth 双保险。
4. **定期备份**：数据库单文件，直接拷走即可（记得连同 `APP_SECRET` 一起保存）。

```bash
cp /vol1/1000/docker/llm-token-hub/data/data.db ~/backup/llm-token-hub-$(date +%F).db
```

---

## 5. 运维命令速查

```bash
cd /vol1/1000/docker/llm-token-hub
docker compose ps          # 状态
docker compose logs -f     # 实时日志
docker compose restart     # 重启
docker compose pull && docker compose up -d   # 升级镜像
docker compose down        # 停止并移除容器（数据卷保留）
```

---

## 6. 故障排查表

| 现象 | 原因 | 处理 |
|------|------|------|
| 页面打不开 | 容器没起来 / 端口冲突 | `docker compose logs -f`；`ss -lntp \| grep 8787` 换端口 |
| 登录一直失败 | `ADMIN_PASSWORD` 与输入不一致 | 改 `.env` 后 `docker compose up -d` 重建 |
| 提示「解密失败，请重新录入 Key」 | `APP_SECRET` 被改过 | 用原密钥，或删库重录 |
| 余额一直显示查询失败 | NAS 出网受限 / 平台接口变更 | 容器内 `curl -I https://api.deepseek.com` 测试；更新 `PLATFORMS` 配置 |
| 镜像拉不动 | GHCR 不可达 | 用方式 C 本地构建 |
| 手机能开但 Key 复制失败 | 浏览器非 HTTPS 环境限制剪贴板 | 用 http 内网访问通常可用；失败时会弹窗供手动复制 |
| 模型库「从平台导入模型」为空/报错 | 该平台不支持 OpenAI 兼容 `/v1/models`，或账号 Key 无权限 | 部分平台需带 `/compatible-mode` 等前缀的 Base URL，可在「编辑」里改该账号的 API Base URL 后重试；或直接「+ 添加」手动录入模型名 |
| 点「AI 生成说明」提示未配置 | 还没在「设置 → AI 模型说明配置」填接口 | 填入任意 OpenAI 兼容的 Base URL、模型名与 API Key 后重试 |

---

## 7. 交给其他 WorkBuddy 执行的提示词

复制下面这段即可，它就能在你本地完成部署和验收：

> 在飞牛 NAS（SSH：root@<NAS内网IP>，密码见我提供的信息）上部署 `/workspace/llm-token-hub` 这个 Docker 项目：
> 1. 用 scp 把项目传到 NAS 的 `/vol1/1000/docker/`；
> 2. SSH 登录后执行 `sudo bash deploy/fnos-deploy.sh --password '<Web密码>' --secret '<随机字符串>' --port 8787`；
> 3. 镜像拉不到就改成 `build: .` 本地构建；
> 4. 执行 `sudo bash deploy/verify.sh --password '<Web密码>'` 做验收，9 项全绿才算完成；
> 5. 最后告诉我可访问的 http 地址，并提醒我：登录密码与加密密钥都可在网页「设置」里随时修改并持久化，需在密码管理器里备份。
