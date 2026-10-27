"""
LLM Token Hub —— 多平台大模型 API Token 管理中枢（网页版 / 容器部署）

· 服务端：FastAPI + SQLite，Docker 一键部署到家庭 NAS，手机浏览器直接访问
· Key 使用主密钥派生（APP_SECRET / ADMIN_PASSWORD）AES(Fernet) 加密落盘，数据库泄露也不暴露明文
· 服务端代理查询各平台余额，前端无跨域问题
· 访问控制：ADMIN_PASSWORD 环境变量设置访问密码，登录后下发 Cookie 会话
"""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
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

source = APP_SECRET if APP_SECRET else "llm-token-hub::" + ADMIN_PASSWORD
fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest()))

app = FastAPI(title="LLM Token Hub", version="1.0.0")

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
    conn.commit()
    return conn

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
    if str(body.get("password", "")) != ADMIN_PASSWORD:
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

# ---------------- 静态页面 ----------------
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.get("/")
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))

@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": app.version}
