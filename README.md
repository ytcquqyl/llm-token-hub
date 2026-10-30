# LLM Token Hub · 多平台大模型 API Token 管理中枢

一个部署在家庭 NAS / 服务器上的**自托管网页工具**：把你散落在各个大模型平台的 API Key **集中加密托管**，
在同一界面**一键复制**、**一键刷新各平台剩余额度**，并提供**直达官方控制台**的入口去创建新 Key。

手机浏览器打开即用，界面为移动端优先的自适应布局。数据只存在你自己的机器上，不经过任何第三方服务器。

---

## 一、能做什么 / 不能做什么

| 能力 | 状态 | 说明 |
|------|------|------|
| 集中托管多个平台 API Key | ✅ 支持 | 加密落盘，列表化管理，备注、Base URL 可自定义 |
| 一键复制 Key | ✅ 支持 | 点「复制 Key」直接写入剪贴板，列表页默认不显示明文 |
| 额度看板（已用/剩余） | ✅ 部分支持 | DeepSeek / 智谱 GLM / Moonshot(Kimi) / 硅基流动 可实时查询 |
| 一键刷新全部额度 | ✅ 支持 | 服务端并发代理查询，避免浏览器跨域限制 |
| 自动创建新 Token | ⚠️ 受平台限制 | **各家均未开放免登录的 Key 创建 API**，本项目提供各平台控制台直达入口：App 内打开 → 网页创建 → 复制回来粘贴即可 |
| 复制 API 地址 / 一键复制「地址+Key」 | ✅ 支持 | 除「复制 Key」外，可单独复制 Base URL，或复制可直接粘贴进客户端的 `base_url + api_key` 配置片段 |
| 模型库（模型清单 + 适用场景说明） | ✅ 支持 | 「模型库」页可从平台 OpenAI 兼容 `/v1/models` 接口导入模型，并为每个模型手写适用场景说明 |
| 模型来源平台标注 + 搜索 | ✅ 支持 | 每个模型显示来源平台徽章与账号备注，模型库支持按名称 / 说明 / 来源实时搜索（v1.3） |
| AI 自动生成适用场景 | ✅ 支持 | 在「设置」里配置一个 OpenAI 兼容接口后，一键让 AI 为某个模型生成「能力特点 + 适用场景」说明 |

### 各平台余额接口现状

| 平台 | 余额查询 | 接口 |
|------|----------|------|
| DeepSeek | ✅ | `GET https://api.deepseek.com/user/balance` |
| 智谱 GLM | ✅ | `GET https://open.bigmodel.cn/api/paas/v4/users/me/balance` |
| Moonshot / Kimi | ✅ | `GET https://api.moonshot.cn/v1/users/me/balance` |
| 硅基流动 | ✅ | `GET https://api.siliconflow.cn/v1/user/info` |
| OpenAI | ❌ | 无公开余额接口（dashboard billing 接口已不稳定），UI 降级为跳转控制台 |
| 通义千问 DashScope | ❌ | 同上 |
| 自定义平台 | ❌ | 仅做 Key 托管与复制 |

> 接口均基于各平台公开文档整理，属"非承诺级"接口，平台变更时需同步更新 `app/main.py` 中的 `PLATFORMS` 配置表。

---

## 二、容器一键部署（推荐）

### 方式 A：docker compose

```bash
mkdir -p /opt/llm-token-hub && cd /opt/llm-token-hub
# 把 docker-compose.yml 放到这里，或直接 git clone 本仓库
docker compose up -d
```

首次使用本地构建：把 `docker-compose.yml` 里的 `image:` 换成 `build: .`，然后 `docker compose up -d --build`。

### 方式 B：docker run

```bash
docker run -d --name llm-token-hub \
  --restart unless-stopped \
  -p 8787:8787 \
  -e ADMIN_PASSWORD="你的强密码" \
  -e APP_SECRET="一串随机长字符串" \
  -e TZ=Asia/Shanghai \
  -v /opt/llm-token-hub/data:/app/data \
  ghcr.io/ytcquqyl/llm-token-hub:latest
```

访问：`http://<NAS内网IP>:8787`，输入 `ADMIN_PASSWORD` 登录。

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `ADMIN_PASSWORD` | ✅ | Web 访问密码（初始值，部署后可在页面「设置」中修改并持久化） |
| `APP_SECRET` | 建议 | API Key 加密主密钥（初始值；未设置则自动随机生成并入库）。部署后可在页面「设置」中修改，修改会用新密钥重加密全部 Key |
| `DB_PATH` | 可选 | SQLite 路径，默认 `/app/data/data.db`（务必落在挂载卷内） |
| `SESSION_TTL` | 可选 | 会话时长（秒），默认 43200（12 小时） |

### 群晖 / 威联通 / 飞牛 OS

1. 套件中心安装 **Container Manager / Docker**，新建项目粘贴 `docker-compose.yml`；
2. 数据卷映射到共享文件夹（如 `/volume1/docker/llm-token-hub/data`）；
3. 端口 8787 放行；手机与 NAS 同局域网即可用 `http://192.168.x.x:8787` 访问。

> 建议给容器配置固定内网 IP + 路由器 DHCP 保留地址（爱快可在「静态分配」里按 MAC 绑定），避免 IP 变化。

---

## 三、模型库与 AI 适用场景说明（v1.2）

除托管 Key 外，v1.2 新增「**模型库**」页，帮你把"每个模型适合干什么"沉淀下来：

- **从平台导入模型**：在模型库点「从平台导入模型」→ 选择一个已添加的账号 → 自动调用该账号平台的 OpenAI 兼容 `/v1/models` 接口拉取模型列表 → 勾选后导入。支持 DeepSeek / 硅基流动 / Moonshot / OpenAI / 通义千问 等所有兼容 `/v1/models` 的平台。
- **手写适用场景说明**：每个模型可编辑一段说明 / 备注（如"擅长中文写作、代码生成、知识问答"），团队共享一份模型选型笔记。
- **AI 自动生成说明**：在「设置 → AI 模型说明配置」里填一个 OpenAI 兼容接口的 Base URL、模型名与 API Key（密钥同样以加密方式存储），之后在模型库点「AI 生成说明」，系统会把模型名发给该 AI，由其返回该模型的能力特点与 3–5 个适用场景并自动回填。
- **一键复制模型名**：便于直接粘贴进代码或对话客户端。

> ⚠️ AI 说明依赖你自配的第三方大模型接口，生成内容为模型回答，**请自行核对准确性**，本工具不对其正确性背书。AI Key 与账号 Key 使用同一主密钥加密，修改加密密钥时会一并重加密。

### v1.3 新增：来源平台标注与模型库搜索

- **来源平台徽章**：从平台导入的模型，卡片标题旁直接显示来源平台徽章（如 `DeepSeek`、`智谱 GLM`），来源行同时记录「平台 · 账号备注」；手动添加的模型标注「手动」。
- **模型库搜索**：模型库页顶部新增搜索框，按模型名 / 适用场景说明 / 来源平台实时过滤，手机上也好用。

---

## 四、在页面内修改登录密码与加密密钥（v1.1 新增）

容器创建时通过环境变量设的 `ADMIN_PASSWORD` / `APP_SECRET` 只是**初始值**。部署后，两者都可以在网页里随时修改并持久化，**无需重建容器、重启也不丢**。

- **修改登录密码**：顶栏「设置 → 修改登录密码」，填当前密码 + 新密码（≥6 位）。修改后所有已登录会话立即失效，需重新登录。
- **修改加密密钥**：「设置 → 修改加密密钥」，填登录密码（确认身份）+ 新密钥（≥8 位）。提交后会**用新密钥原子地重新加密库内全部 Key**；若任意一条旧 Key 解密失败，整体中止、不落脏数据。

> ⚠️ 加密密钥改完，旧的 `APP_SECRET` 即失效；若日后需迁移 / 恢复备份，请用**新密钥**对应的数据。修改前建议先备份 `data/data.db`。

---

## 五、安全设计

- **Key 不明文落盘**：API Key 以 Fernet（AES-128-CBC + HMAC）加密，主密钥由 `APP_SECRET` 经 SHA-256 派生。主密钥在首次运行时落库到数据卷的 `settings` 表（容器创建时可用环境变量 `APP_SECRET` 指定；未指定则自动随机生成）。因此**保护边界从「仅环境变量」变为「整个数据卷」**：数据库与密钥同处 `data/` 目录，请对该目录统一做好访问控制与备份加密（已实测：库内无 `sk-` 明文字符串）。
- **访问鉴权**：未登录访问任意 `/api/*` 返回 401，登录后下发 HttpOnly Cookie，默认 12 小时过期。
- **明文按需取用**：列表页不渲染明文，点「复制」时才向服务端请求一次。
- **AI 配置密钥同密**：「设置 → AI 模型说明配置」里的 API Key 与账号 Key 使用同一主密钥（Fernet）加密落盘，修改加密密钥时会一并重加密；同样实测库内无明文字符串。
- **网络最小化**：仅服务端向各平台官方域名发 HTTPS 请求，无遥测、无外发。

生产建议：
1. 只在内网 / VPN（Tailscale、WireGuard）暴露，或前面挂反向代理 + HTTPS；
2. 用 Nginx/Caddy 反代并加 Basic Auth 双保险；
3. 定期备份 `data/data.db`（备份本身已包含主密钥与全部数据；但迁移到新机器时**必须连同 `settings` 表中的密钥一起**，单独拷走旧 `APP_SECRET` 变量已无法解密新密钥加密的数据）。

---

## 六、接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 密码登录，下发会话 Cookie |
| POST | `/api/logout` | 退出登录 |
| GET | `/api/platforms` | 平台配置与常用模型 |
| GET | `/api/accounts` | 账户列表 |
| POST | `/api/accounts` | 新增 Key |
| PUT | `/api/accounts/{id}` | 修改（api_key 留空表示不改） |
| DELETE | `/api/accounts/{id}` | 删除 |
| GET | `/api/accounts/{id}/key` | 取明文 Key |
| POST | `/api/accounts/{id}/refresh` | 刷新单个额度 |
| POST | `/api/refresh-all` | 刷新全部额度 |
| GET | `/api/settings` | 设置状态（是否有 Key、密钥是否已托管） |
| POST | `/api/settings/password` | 修改登录密码 |
| POST | `/api/settings/secret` | 修改加密密钥并重加密全部 Key 与 AI Key |
| POST | `/api/accounts/{id}/models` | 调用该账号平台的 `/v1/models` 拉取模型列表 |
| GET | `/api/models` | 模型库列表（含 AI 是否已配置） |
| POST | `/api/models` | 新增模型（name / description / source） |
| PUT | `/api/models/{id}` | 修改模型说明 |
| DELETE | `/api/models/{id}` | 删除模型 |
| POST | `/api/models/{id}/ai-describe` | 用已配置的 AI 接口为模型生成适用场景说明 |
| GET | `/api/settings/ai` | AI 配置状态（返回地址/模型名，不含密钥） |
| POST | `/api/settings/ai` | 保存 AI 接口配置（密钥加密存储） |
| GET | `/healthz` | 健康检查（Docker HEALTHCHECK 使用） |

---

## 七、本地开发

```bash
pip install -r requirements.txt
ADMIN_PASSWORD=dev APP_SECRET=dev DB_PATH=./data/data.db \
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8787
```

## 八、路线图

- [x] 页面内修改登录密码与加密密钥（v1.1）
- [x] 复制 API 地址 / 一键复制「地址+Key」配置片段（v1.2）
- [x] 模型库：从平台 `/v1/models` 导入模型 + 适用场景说明（v1.2）
- [x] AI 自动生成模型适用场景说明（v1.2）
- [ ] 自定义平台支持手动填写余额接口路径
- [ ] 额度变化历史与趋势图
- [ ] 低余额提醒（Webhook / 邮件 / Bark）
- [ ] 硅基流动等开放平台尝试在线创建 Key（待平台能力确认）

## 九、从 GHCR 拉取镜像（NAS 部署推荐）

项目已接入 GitHub Actions：推送到 `main` 或打 `v*` tag 后，自动构建多架构镜像（amd64 / arm64）并发布到 GHCR。

```bash
# 在 NAS 上
docker pull ghcr.io/ytcquqyl/llm-token-hub:latest
cd /vol1/1000/docker/llm-token-hub && docker compose up -d
```

若 `docker pull` 提示无权限：到 GitHub 个人页 → **Packages** → `llm-token-hub` → Package settings → **Change visibility → Public**；
或在 NAS 上登录：`echo <PAT> | docker login ghcr.io -u ytcquqyl --password-stdin`。

在本机手动发布一次（建库 + 推送 + 等构建）：

```bash
bash deploy/publish-github.sh
```

## 十、许可

MIT License

### v1.4.1 安全加固

- 登录密码改为 **PBKDF2-SHA256 带盐慢哈希**（30 万次迭代），旧无盐哈希在下次登录成功时自动升级
- 登录接口**限速**：连续失败 3 次后指数退避（最长封禁 256 秒），防在线爆破
- 登出改为**服务端销毁会话**（此前仅删 Cookie，会话仍有效至过期）
- 新增安全响应头（CSP / X-Frame-Options / nosniff / Referrer-Policy）
- HTTPS 访问时 Cookie 自动带 `Secure` 标记；容器改为非 root 用户运行（uid 1000，需求数据目录属主）
