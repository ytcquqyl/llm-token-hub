"""
LLM Token Hub —— 多平台大模型 API Token 管理中枢（网页版 / 容器部署）

· 服务端：FastAPI + SQLite，Docker 一键部署到家庭 NAS，手机浏览器直接访问
· Key 使用主密钥派生（APP_SECRET / ADMIN_PASSWORD）AES(Fernet) 加密落盘，数据库泄露也不暴露明文
· 登录密码与加密密钥均支持在页面内修改并持久化（默认仅容器创建时通过环境变量设定）
· 修改加密密钥会原子地用新密钥重新加密库内全部 Key
· 服务端代理查询各平台余额，前端无跨域问题
· 模型库：从平台 OpenAI 兼容 /v1/models 拉取模型，可为每个模型记录适用场景说明
· AI 说明：配置一个 OpenAI 兼容接口后，一键让 AI 为模型生成适用场景说明
· 复制增强：可单独复制 API 地址，或一键复制「地址 + Key」配置
· 访问控制：密码登录后下发 HttpOnly Cookie 会话
"""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "data" / "data.db"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
APP_SECRET = os.environ.get("APP_SECRET", "")
SESSION_TTL = int(os.environ.get("SESSION_TTL", str(12 * 3600)))  # 会话时长，默认 12 小时

# 模块级全局密钥对象；启动后由 init_app 与修改密钥接口重建
_source = APP_SECRET if APP_SECRET else "llm-token-hub::" + ADMIN_PASSWORD
fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(_source.encode()).digest()))


# ---------------- 平台配置表 ----------------
# balance = None 表示该平台未开放公开的余额查询接口，UI 降级为「跳转控制台查看」
PLATFORMS = {
    "deepseek": {
        "display": "DeepSeek",
        "console": "https://platform.deepseek.com/api_keys",
        "base_url": "https://api.deepseek.com",
        "balance": {"method": "GET", "path": "/user/balance", "auth": "Bearer {key}",
                    "json_path": "balance_infos.0.total_balance", "currency": "CNY"},
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "siliconflow": {
        "display": "硅基流动 SiliconFlow",
        "console": "https://cloud.siliconflow.cn/account/ak",
        "base_url": "https://api.siliconflow.cn",
        "balance": {"method": "GET", "path": "/v1/user/info", "auth": "Bearer {key}",
                    "json_path": "data.balance", "currency": "CNY"},
        "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3"],
    },
    "moonshot": {
        "display": "Moonshot / Kimi",
        "console": "https://platform.moonshot.cn/console/api-keys",
        "base_url": "https://api.moonshot.cn",
        "balance": {"method": "GET", "path": "/v1/users/me/balance", "auth": "Bearer {key}",
                    "json_path": "data.available_balance", "currency": "CNY"},
        "models": ["moonshot-v1-8k", "kimi-k2-0711-preview"],
    },
    "zhipu": {
        "display": "智谱 GLM",
        "console": "https://open.bigmodel.cn/usercenter/apikeys",
        "base_url": "https://open.bigmodel.cn",
        "balance": {"method": "GET", "path": "/api/paas/v4/users/me/balance", "auth": "{key}",
                    "json_path": "data.available_balance", "currency": "CNY"},
        "models": ["glm-4-plus", "glm-4-flash"],
    },
    "openai": {
        "display": "OpenAI",
        "console": "https://platform.openai.com/api-keys",
        "base_url": "https://api.openai.com",
        "balance": None,
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    "dashscope": {
        "display": "通义千问 DashScope",
        "console": "https://bailian.console.aliyun.com/?apiKey=1",
        "base_url": "https://dashscope.aliyuncs.com",
        "balance": None,
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
    },
}


# ---------------- 数据库 ----------------
def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 读写并发，减少锁争用
    conn.execute("""CREATE TABLE IF NOT EXISTS accounts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        key_encrypted TEXT NOT NULL,
        base_url TEXT NOT NULL DEFAULT '',
        remark TEXT NOT NULL DEFAULT '',
        balance REAL,
        balance_currency TEXT NOT NULL DEFAULT '',
        balance_status TEXT NOT NULL DEFAULT '',
        balance_updated_at INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS models(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        source_label TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL)""")
    try:  # v1.2.1 迁移：为旧库补充来源标注列
        conn.execute("ALTER TABLE models ADD COLUMN source_label TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    return conn


# ---------------- 设置读写 ----------------
def get_setting(key: str, default=None):
    conn = db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = db()
    try:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


# ---------------- AI 说明配置（key 同样用主密钥加密） ----------------
def get_ai_config() -> dict:
    conn = db()
    try:
        rows = conn.execute(
            "SELECT key,value FROM settings WHERE key IN ('ai_base_url','ai_model','ai_key_encrypted')"
        ).fetchall()
    finally:
        conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_ai_config(base_url: str, model: str, key: str):
    """key 为空时保留原有密钥（用于仅更新地址/模型名）。"""
    conn = db()
    try:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('ai_base_url',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (base_url,))
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('ai_model',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (model,))
        if key:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('ai_key_encrypted',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encrypt_key(key),))
        conn.commit()
    finally:
        conn.close()


def rebuild_fernet(secret: str):
    global fernet
    fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


# ---------------- 启动初始化 ----------------
def init_app():
    """首启：用环境变量为密码/密钥播种；之后以库内设置为准。"""
    if get_setting("admin_pw_hash") is None:
        set_setting("admin_pw_hash", hash_pw(ADMIN_PASSWORD))
    secret = get_setting("app_secret")
    if secret is None:
        secret = APP_SECRET if APP_SECRET else secrets.token_urlsafe(32)
        set_setting("app_secret", secret)
    rebuild_fernet(secret)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_app()
    yield


app = FastAPI(title="LLM Token Hub", version="1.2.1", lifespan=lifespan)


# ---------------- 会话 ----------------
SESSIONS: dict = {}


def make_session() -> str:
    now = time.time()
    for k in [k for k, v in SESSIONS.items() if v < now]:
        SESSIONS.pop(k, None)
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = now + SESSION_TTL
    return token


def require_session(request: Request) -> str:
    token = request.cookies.get("hub_session", "")
    if not token or SESSIONS.get(token, 0) < time.time():
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return token


# ---------------- 工具 ----------------
def encrypt_key(plain: str) -> str:
    return fernet.encrypt(plain.encode()).decode()


def decrypt_key(cipher: str) -> str:
    return fernet.decrypt(cipher.encode()).decode()


def mask(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "*" * max(4, len(key) - 8) + key[-4:]


def get_by_path(obj, path: str):
    cur = obj
    for p in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def row_to_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "platform": r["platform"],
        "platform_display": PLATFORMS.get(r["platform"], {}).get("display", r["platform"]),
        "label": r["label"],
        "base_url": r["base_url"],
        "remark": r["remark"],
        "key_masked": "",
        "balance": r["balance"],
        "balance_currency": r["balance_currency"],
        "balance_status": r["balance_status"],
        "balance_updated_at": r["balance_updated_at"],
        "console": PLATFORMS.get(r["platform"], {}).get("console", ""),
        "support_balance": PLATFORMS.get(r["platform"], {}).get("balance") is not None,
    }


# ---------------- 接口 ----------------
@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if hash_pw(str(body.get("password", ""))) != get_setting("admin_pw_hash"):
        return JSONResponse({"ok": False, "msg": "密码错误"}, status_code=401)
    token = make_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie("hub_session", token, httponly=True, samesite="lax", max_age=SESSION_TTL)
    return resp


@app.post("/api/logout")
async def logout(_: str = Depends(require_session)):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("hub_session")
    return resp


@app.get("/api/platforms")
async def platforms(_: str = Depends(require_session)):
    return {"items": [
        {"name": k, "display": v["display"], "console": v["console"],
         "base_url": v["base_url"], "models": v["models"],
         "support_balance": v["balance"] is not None}
        for k, v in PLATFORMS.items()
    ]}


@app.get("/api/accounts")
async def list_accounts(_: str = Depends(require_session)):
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
        items = [row_to_dict(r) for r in rows]
        return {"items": items}
    finally:
        conn.close()


@app.post("/api/accounts")
async def add_account(request: Request, _: str = Depends(require_session)):
    body = await request.json()
    platform = str(body.get("platform", "")).strip()
    key = str(body.get("api_key", "")).strip()
    if not key:
        return JSONResponse({"ok": False, "msg": "API Key 不能为空"}, status_code=400)
    cfg = PLATFORMS.get(platform)
    if cfg is None and platform != "custom":
        return JSONResponse({"ok": False, "msg": "未知平台"}, status_code=400)
    base_url = str(body.get("base_url", "")).strip() or (cfg["base_url"] if cfg else "")
    conn = db()
    try:
        conn.execute(
            "INSERT INTO accounts(platform,label,key_encrypted,base_url,remark,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (platform, str(body.get("label", "")).strip() or (cfg["display"] if cfg else platform),
             encrypt_key(key), base_url, str(body.get("remark", "")), int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.put("/api/accounts/{account_id}")
async def update_account(account_id: int, request: Request, _: str = Depends(require_session)):
    body = await request.json()
    conn = db()
    try:
        r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        label = str(body.get("label", r["label"]))
        base_url = str(body.get("base_url", r["base_url"]))
        remark = str(body.get("remark", r["remark"]))
        new_key = str(body.get("api_key", "")).strip()
        if new_key:
            conn.execute("UPDATE accounts SET label=?,base_url=?,remark=?,key_encrypted=? WHERE id=?",
                         (label, base_url, remark, encrypt_key(new_key), account_id))
        else:
            conn.execute("UPDATE accounts SET label=?,base_url=?,remark=? WHERE id=?",
                         (label, base_url, remark, account_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int, _: str = Depends(require_session)):
    conn = db()
    try:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/accounts/{account_id}/key")
async def reveal_key(account_id: int, _: str = Depends(require_session)):
    """返回明文 Key（前端仅在使用时请求，不常驻页面）"""
    conn = db()
    try:
        r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        try:
            plain = decrypt_key(r["key_encrypted"])
        except InvalidToken:
            return JSONResponse({"ok": False, "msg": "解密失败，主密钥已变更，请重新录入 Key"},
                                status_code=400)
        return {"ok": True, "api_key": plain, "masked": mask(plain)}
    finally:
        conn.close()


@app.post("/api/accounts/{account_id}/refresh")
async def refresh_one(account_id: int, _: str = Depends(require_session)):
    conn = db()
    try:
        r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        result = await query_balance(r)
        conn.execute("UPDATE accounts SET balance=?,balance_currency=?,balance_status=?,balance_updated_at=? WHERE id=?",
                     (result["balance"], result["currency"], result["status"],
                      int(time.time()), account_id))
        conn.commit()
        return {"ok": result["ok"], "msg": result["msg"], "balance": result["balance"],
                "currency": result["currency"]}
    finally:
        conn.close()


@app.post("/api/refresh-all")
async def refresh_all(_: str = Depends(require_session)):
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM accounts").fetchall()
        ok_count = 0
        failed = []
        for r in rows:
            result = await query_balance(r)
            conn.execute(
                "UPDATE accounts SET balance=?,balance_currency=?,balance_status=?,balance_updated_at=? WHERE id=?",
                (result["balance"], result["currency"], result["status"], int(time.time()), r["id"]))
            if result["ok"]:
                ok_count += 1
            elif result["status"] not in ("unsupported", ""):
                failed.append(f"{PLATFORMS.get(r['platform'], {}).get('display', r['platform'])}: {result['msg']}")
        conn.commit()
        return {"ok": True, "ok_count": ok_count, "failed": failed}
    finally:
        conn.close()


# ---------------- 设置：修改密码 / 加密密钥 ----------------
@app.get("/api/settings")
async def get_settings(_: str = Depends(require_session)):
    conn = db()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
    finally:
        conn.close()
    return {"has_accounts": n > 0, "secret_managed": get_setting("app_secret") is not None}


@app.post("/api/settings/password")
async def change_password(request: Request, _: str = Depends(require_session)):
    body = await request.json()
    cur = str(body.get("current", ""))
    new = str(body.get("new", ""))
    if len(new) < 6:
        return JSONResponse({"ok": False, "msg": "新密码至少 6 位"}, status_code=400)
    if hash_pw(cur) != get_setting("admin_pw_hash"):
        return JSONResponse({"ok": False, "msg": "当前密码错误"}, status_code=403)
    set_setting("admin_pw_hash", hash_pw(new))
    # 密码变更后强制所有会话失效，需重新登录
    SESSIONS.clear()
    return {"ok": True}


@app.post("/api/settings/secret")
async def change_secret(request: Request, _: str = Depends(require_session)):
    body = await request.json()
    login_pw = str(body.get("login_password", ""))
    new_secret = str(body.get("new_secret", ""))
    if len(new_secret) < 8:
        return JSONResponse({"ok": False, "msg": "新加密密钥至少 8 位"}, status_code=400)
    if hash_pw(login_pw) != get_setting("admin_pw_hash"):
        return JSONResponse({"ok": False, "msg": "登录密码错误，无法修改加密密钥"}, status_code=403)
    try:
        reencrypt_all(new_secret)
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    return {"ok": True, "msg": "加密密钥已更新并重加密全部 Key"}


def reencrypt_all(new_secret: str):
    """用新密钥原子地重新加密全部 Key；任一条解密失败则整体中止（不落脏数据）。"""
    global fernet
    old_fernet = fernet
    new_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(new_secret.encode()).digest()))
    conn = db()
    try:
        rows = conn.execute("SELECT id, key_encrypted FROM accounts").fetchall()
        updates = []
        for r in rows:
            try:
                plain = old_fernet.decrypt(r["key_encrypted"].encode()).decode()
            except InvalidToken:
                raise ValueError(f"记录 id={r['id']} 解密失败，已中止（未改动任何数据）")
            updates.append((new_fernet.encrypt(plain.encode()).decode(), r["id"]))
        for enc, rid in updates:
            conn.execute("UPDATE accounts SET key_encrypted=? WHERE id=?", (enc, rid))
        # 同时重加密 AI 密钥
        ai_row = conn.execute("SELECT value FROM settings WHERE key='ai_key_encrypted'").fetchone()
        if ai_row:
            try:
                ai_plain = old_fernet.decrypt(ai_row["value"].encode()).decode()
            except InvalidToken:
                raise ValueError("AI 密钥解密失败，已中止（未改动任何数据）")
            conn.execute("UPDATE settings SET value=? WHERE key='ai_key_encrypted'",
                         (new_fernet.encrypt(ai_plain.encode()).decode(),))
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("app_secret", new_secret),
        )
        conn.commit()
    finally:
        conn.close()
    fernet = new_fernet


async def query_balance(r: sqlite3.Row) -> dict:
    cfg = PLATFORMS.get(r["platform"])
    if cfg is None or cfg["balance"] is None:
        return {"ok": False, "balance": None, "currency": "", "status": "unsupported",
                "msg": "该平台无公开余额接口，请到控制台查看"}
    try:
        plain = decrypt_key(r["key_encrypted"])
    except InvalidToken:
        return {"ok": False, "balance": None, "currency": "", "status": "error",
                "msg": "解密失败，请重新录入 Key"}
    b = cfg["balance"]
    url = (r["base_url"] or cfg["base_url"]) + b["path"]
    headers = {"Authorization": b["auth"].replace("{key}", plain),
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.request(b["method"], url, headers=headers)
        if resp.status_code in (401, 403):
            return {"ok": False, "balance": None, "currency": b["currency"], "status": "invalid",
                    "msg": "Key 无效或权限不足"}
        if resp.status_code >= 400:
            return {"ok": False, "balance": None, "currency": b["currency"], "status": "error",
                    "msg": f"HTTP {resp.status_code}"}
        raw = get_by_path(resp.json(), b["json_path"])
        if raw is None:
            return {"ok": False, "balance": None, "currency": b["currency"], "status": "error",
                    "msg": "响应字段解析失败"}
        value = float(raw)
    except Exception as e:  # 网络异常 / 解析异常统一降级
        return {"ok": False, "balance": None, "currency": b.get("currency", ""), "status": "error",
                "msg": f"网络错误或超时：{type(e).__name__}"}
    return {"ok": True, "balance": value, "currency": b["currency"], "status": "ok", "msg": "ok"}


# ---------------- 模型库 + AI 说明 ----------------
@app.post("/api/accounts/{account_id}/models")
async def query_account_models(account_id: int, _: str = Depends(require_session)):
    """调用账号所属平台的 OpenAI 兼容 /v1/models 接口，返回模型 ID 列表。"""
    conn = db()
    try:
        r = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    finally:
        conn.close()
    if r is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        plain = decrypt_key(r["key_encrypted"])
    except InvalidToken:
        return JSONResponse({"ok": False, "msg": "解密失败，请重新录入 Key"}, status_code=400)
    cfg = PLATFORMS.get(r["platform"])
    base = (r["base_url"] or (cfg["base_url"] if cfg else "")).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = base + "/v1/models"
    headers = {"Authorization": "Bearer " + plain, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code in (401, 403):
            return JSONResponse({"ok": False, "msg": "Key 无效或权限不足"}, status_code=400)
        if resp.status_code >= 400:
            return JSONResponse({"ok": False, "msg": f"HTTP {resp.status_code}"}, status_code=400)
        data = resp.json()
        items = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return {"ok": True, "url": url, "items": items, "count": len(items),
                "platform_display": cfg["display"] if cfg else r["platform"],
                "label": r["label"] or ""}
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"网络错误或超时：{type(e).__name__}"}, status_code=400)


@app.get("/api/models")
async def list_models(_: str = Depends(require_session)):
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM models ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    cfg = get_ai_config()
    return {"items": [dict(r) for r in rows],
            "ai_configured": bool(cfg.get("ai_base_url") and cfg.get("ai_key_encrypted"))}


@app.post("/api/models")
async def add_model(request: Request, _: str = Depends(require_session)):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"ok": False, "msg": "模型名不能为空"}, status_code=400)
    conn = db()
    try:
        cur = conn.execute("SELECT id FROM models WHERE name=?", (name,)).fetchone()
        if cur:
            conn.execute(
                "UPDATE models SET description=?,source=?,source_label=?,updated_at=? WHERE id=?",
                (str(body.get("description", "")), str(body.get("source", "manual")),
                 str(body.get("source_label", "")), int(time.time()), cur["id"]))
            mid = cur["id"]
        else:
            cur = conn.execute(
                "INSERT INTO models(name,description,source,source_label,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (name, str(body.get("description", "")), str(body.get("source", "manual")),
                 str(body.get("source_label", "")), int(time.time()), int(time.time())))
            mid = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "model": dict(row)}


@app.put("/api/models/{model_id}")
async def update_model(model_id: int, request: Request, _: str = Depends(require_session)):
    body = await request.json()
    conn = db()
    try:
        r = conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="模型不存在")
        conn.execute("UPDATE models SET description=?,updated_at=? WHERE id=?",
                     (str(body.get("description", r["description"])), int(time.time()), model_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/models/{model_id}")
async def delete_model(model_id: int, _: str = Depends(require_session)):
    conn = db()
    try:
        conn.execute("DELETE FROM models WHERE id=?", (model_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/models/{model_id}/ai-describe")
async def ai_describe(model_id: int, _: str = Depends(require_session)):
    """用已配置的 OpenAI 兼容接口，为指定模型生成适用场景说明。"""
    cfg = get_ai_config()
    if not cfg.get("ai_base_url") or not cfg.get("ai_key_encrypted"):
        return JSONResponse({"ok": False, "msg": "请先在设置中配置 AI 接口与密钥"}, status_code=400)
    try:
        ai_key = decrypt_key(cfg["ai_key_encrypted"])
    except InvalidToken:
        return JSONResponse({"ok": False, "msg": "AI 密钥解密失败，请重新配置"}, status_code=400)
    conn = db()
    try:
        r = conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    finally:
        conn.close()
    if r is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    prompt = (f"你是一名大模型选型顾问。请简要说明模型「{r['name']}」的主要能力特点，"
              f"并列出它最适用的 3-5 个使用场景。用简体中文、分点回答，总字数控制在 160 字以内，"
              f"不要使用代码块或 Markdown 标题，直接给出文字。")
    base = cfg["ai_base_url"].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = base + "/v1/chat/completions"
    headers = {"Authorization": "Bearer " + ai_key, "Content-Type": "application/json"}
    payload = {"model": cfg["ai_model"] or "gpt-4o-mini",
               "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.6, "max_tokens": 800}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            detail = resp.text[:200]
            return JSONResponse({"ok": False,
                                 "msg": f"AI 接口返回 HTTP {resp.status_code}：{detail}"}, status_code=400)
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        # 逐级兜底解析：content 字符串 / 分段数组 / reasoning_content / choices[0].text
        text = ""
        c = msg.get("content")
        if isinstance(c, str):
            text = c.strip()
        elif isinstance(c, list):
            text = "".join(x.get("text", "") for x in c if isinstance(x, dict)).strip()
        if not text:
            text = str(msg.get("reasoning_content") or "").strip()
        if not text:
            text = str(choice.get("text") or "").strip()
        if not text:
            fr = choice.get("finish_reason")
            raw = json.dumps(data, ensure_ascii=False)[:200]
            return JSONResponse({"ok": False,
                                 "msg": f"AI 返回内容为空（finish_reason={fr}）响应片段：{raw}"},
                                status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"调用 AI 失败：{type(e).__name__}"}, status_code=400)
    conn = db()
    try:
        conn.execute("UPDATE models SET description=?,updated_at=? WHERE id=?",
                     (text, int(time.time()), model_id))
        conn.commit()
        row = conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "description": text, "model": dict(row)}


@app.get("/api/settings/ai")
async def get_ai(_: str = Depends(require_session)):
    cfg = get_ai_config()
    return {"configured": bool(cfg.get("ai_base_url") and cfg.get("ai_key_encrypted")),
            "base_url": cfg.get("ai_base_url", ""), "model": cfg.get("ai_model", "")}


@app.post("/api/settings/ai")
async def save_ai(request: Request, _: str = Depends(require_session)):
    body = await request.json()
    base_url = str(body.get("base_url", "")).strip()
    model = str(body.get("model", "")).strip()
    key = str(body.get("key", "")).strip()
    if not base_url or not model:
        return JSONResponse({"ok": False, "msg": "接口地址与模型名必填"}, status_code=400)
    set_ai_config(base_url, model, key)  # key 为空则保留原值
    return {"ok": True}


# ---------------- 静态页面 ----------------
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": app.version}
