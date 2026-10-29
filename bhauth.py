#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot-hosting 自动重新授权（Discord 用户 token → 新的 session_token）

背景：bot-hosting 的 session_token 是自家 JWT（7 天有效），被服务端判定会话异常时会直接
`disconnect`。只要手里还有 Discord 用户 token，就能走一遍完整 OAuth 静默授权，重新拿到
一个全新的 session_token，全自动恢复，不用人工登录取 cookie。

用法（workflow 里，在续期脚本之前跑）：
  env:
    DISCORD_TOKEN: ${{ secrets.DISCORD_TOKEN_2 }}
    SECRET_NAME:   SESSION_TOKEN_2
    GH_TOKEN:      ${{ secrets.GH_TOKEN }}
  run: python bhauth.py

成功后会:
  1) 输出 SESSION_TOKEN=<新值> 到 $GITHUB_ENV（供后续步骤使用）
  2) 用 gh secret set 把新 token / user_id / username 写回 Secrets

仅依赖标准库（urllib），不需要 requests。
"""
import os, re, sys, json, subprocess, urllib.parse, urllib.request, urllib.error
import http.cookiejar

BASE = "https://bot-hosting.net"
CLIENT_ID = os.environ.get("BH_DISCORD_CLIENT_ID", "884382422530158623")
REDIRECT = f"{BASE}/login"
DISCORD_TOKEN = (os.environ.get("DISCORD_TOKEN") or "").strip()
SECRET_NAME = os.environ.get("SECRET_NAME", "SESSION_TOKEN_2")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

suffix = "_2" if SECRET_NAME.endswith("_2") else ""
SIDE_SECRETS = {
    "USER_ID": os.environ.get("USER_ID_SECRET_NAME", f"USER_ID{suffix}"),
    "USERNAME": os.environ.get("USERNAME_SECRET_NAME", f"USERNAME{suffix}"),
}


def log(m):
    print(m, flush=True)


def gh_set(name, value):
    if not value:
        return False
    try:
        env = dict(os.environ)
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN
        p = subprocess.run(["gh", "secret", "set", name, "--body", value],
                           capture_output=True, text=True, timeout=40, env=env)
        if p.returncode == 0:
            log(f"✅ Secret 已更新: {name}")
            return True
        log(f"❌ Secret {name} 更新失败: {p.stderr.strip()[:200]}")
    except Exception as e:
        log(f"❌ Secret {name} 异常: {e}")
    return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    if not DISCORD_TOKEN:
        log("ℹ️ 未配置 DISCORD_TOKEN，跳过自动重新授权")
        return 0

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener_noredir = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), NoRedirect())
    hdr = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

    # 1) 拿 bot-hosting 的 state（这步会下发 oauth_state_discord cookie，缺了会 error=csrf）
    try:
        opener_noredir.open(urllib.request.Request(f"{BASE}/login/discord", headers=hdr), timeout=30)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
    except Exception as e:
        log(f"❌ 请求 /login/discord 失败: {e}")
        return 1
    else:
        loc = ""
    m = re.search(r"[?&]state=([^&]+)", loc or "")
    if not m:
        log(f"❌ 拿不到 OAuth state: location={str(loc)[:120]}")
        return 1
    state = m.group(1)
    log(f"🔑 state={state[:10]}…")

    # 2) 用 Discord 用户 token 完成静默授权，换 code
    qs = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": "identify email",
        "redirect_uri": REDIRECT,
        "state": state,
    }, quote_via=urllib.parse.quote)
    body = json.dumps({"permissions": "0", "authorize": True}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v9/oauth2/authorize?{qs}", data=body, method="POST",
        headers={**hdr, "Authorization": DISCORD_TOKEN, "Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        log(f"❌ Discord 授权失败: HTTP {e.code} {e.read()[:200].decode('utf-8', 'ignore')}")
        return 1
    loc2 = (data or {}).get("location", "")
    if "code=" not in loc2:
        log(f"❌ 授权响应里没有 code: {loc2[:160]}")
        return 1
    log("✅ 已获得授权 code")

    # 3) 带着 state cookie 访问回调，服务端会下发新的 session_token
    try:
        r3 = opener.open(urllib.request.Request(loc2, headers=hdr), timeout=40)
        final_url = r3.geturl()
    except Exception as e:
        log(f"❌ 回调请求失败: {e}")
        return 1

    cookies = {c.name: c.value for c in jar}
    tok = cookies.get("session_token")
    uid = cookies.get("user_id")
    uname = cookies.get("username")
    if not tok:
        log(f"❌ 回调后没有 session_token（最终 URL: {final_url}）")
        return 1
    log(f"✅ 拿到新 session_token (len={len(tok)}), user_id={uid}, username={uname}")

    # 4) 验证登录态
    try:
        r4 = opener.open(urllib.request.Request(f"{BASE}/a/billings", headers=hdr), timeout=40)
        if "/login" in r4.geturl():
            log(f"❌ 新 token 验证失败，仍被重定向到 {r4.geturl()}")
            return 1
    except Exception as e:
        log(f"❌ 验证登录态失败: {e}")
        return 1
    log("✅ 登录态验证通过（已到达账单页）")

    # 5) 回写 secrets
    gh_set(SECRET_NAME, tok)
    gh_set(SIDE_SECRETS["USER_ID"], uid)
    gh_set(SIDE_SECRETS["USERNAME"], uname)

    env_file = os.environ.get("GITHUB_ENV")
    if env_file:
        with open(env_file, "a") as f:
            f.write(f"SESSION_TOKEN={tok}\nUSER_ID={uid}\nUSERNAME={uname}\n")
        log("✅ 已写入 GITHUB_ENV")
    return 0


if __name__ == "__main__":
    sys.exit(main())
