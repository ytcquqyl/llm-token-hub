#!/usr/bin/env bash
# LLM Token Hub 部署验收脚本：依次检查容器状态 / 健康检查 / 登录鉴权 / 加解密往返
# 用法：sudo bash deploy/verify.sh --port 8787 --password '你的Web密码'
set -uo pipefail
PORT="${PORT:-8787}"
PASSWORD="${PASSWORD:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --password) PASSWORD="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -z "$PASSWORD" ]] && read -rsp "输入 Web 登录密码: " PASSWORD && echo
BASE="http://127.0.0.1:${PORT}"
pass=0; fail=0
check() { if [[ "$1" == "0" ]]; then echo "  [OK] $2"; pass=$((pass+1)); else echo "  [FAIL] $2"; fail=$((fail+1)); fi; }

echo "== 1. 容器状态 =="
docker ps --filter name=llm-token-hub --format '  {{.Status}}' | head -1
docker ps --filter name=llm-token-hub --format '{{.Status}}' | grep -q "healthy\|Up" ; check $? "容器处于 Up 状态"

echo "== 2. 健康检查 =="
curl -fsS "$BASE/healthz" >/dev/null; check $? "/healthz 返回 200"

echo "== 3. 未登录鉴权（应为 401）=="
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/accounts")
[[ "$code" == "401" ]]; check $? "未登录访问返回 401（实际 $code）"

echo "== 4. 登录 =="
curl -fsS -c /tmp/hub_ck -X POST "$BASE/api/login" -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PASSWORD\"}" >/dev/null; check $? "使用密码登录成功"

echo "== 5. 写入并读回 Key（验证加密链路）=="
TESTKEY="sk-verify-$(date +%s)"
curl -fsS -b /tmp/hub_ck -X POST "$BASE/api/accounts" -H 'Content-Type: application/json' \
  -d "{\"platform\":\"deepseek\",\"label\":\"自检记录\",\"api_key\":\"$TESTKEY\"}" >/dev/null
check $? "写入测试记录"
GOT=$(curl -fsS -b /tmp/hub_ck "$BASE/api/accounts" | grep -o '"id":[0-9]*' | head -1 | grep -o '[0-9]*')
PLAIN=$(curl -fsS -b /tmp/hub_ck "$BASE/api/accounts/$GOT/key" | grep -o '"api_key":"[^"]*"' | cut -d'"' -f4)
[[ "$PLAIN" == "$TESTKEY" ]]; check $? "解密读回明文一致（$PLAIN）"

echo "== 6. 落盘无明文 =="
DB=$(docker inspect llm-token-hub --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}')
if [[ -n "$DB" && -f "$DB/data.db" ]]; then
  ! grep -qa "$TESTKEY" "$DB/data.db"; check $? "数据库文件中不含明文 Key"
else
  echo "  [跳过] 未找到挂载的 data.db"
fi

echo "== 7. 清理测试数据 =="
curl -fsS -b /tmp/hub_ck -X DELETE "$BASE/api/accounts/$GOT" >/dev/null; check $? "已删除自检记录"
rm -f /tmp/hub_ck

echo
echo "通过 $pass 项，失败 $fail 项"
[[ $fail -eq 0 ]] && echo "验收结论：部署正常，可用浏览器访问" || echo "验收结论：存在问题，请执行 docker compose logs -f 排查"
exit $fail
