#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, requests, subprocess
from datetime import datetime
from seleniumbase import SB

# 环境变量配置(可以直接私库在双引号里填写)
EMAIL         = os.environ.get("EMAIL") or "xxxxx@gmail.com"   # 邮箱,只用于通知使用，可随意填写
SESSION_TOKEN = os.environ.get("SESSION_TOKEN") or ""          # session token，必须填写
GH_TOKEN      = os.environ.get("GH_TOKEN") or ""               # GitHub PAT token,用于自动更新session token,必须填写
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""             # TG chat id,不填写不通知，需和bot token一起填写生效
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""           # TG bot token 

if not SESSION_TOKEN :
    print("ℹ️ 未配置 SESSION_TOKEN,脚本终止。")
    sys.exit(1)

# 构造cookie
COOKIES = {
    "session_token": SESSION_TOKEN,
    "login": "true",
    "theme": "system",
}

# 获取cookie到期时间
def get_cookie_info(sb, name):
    cookies = sb.get_cookies()
    for c in cookies:
        if c.get('name') == name:
            value = c.get('value')
            expiry_ts = c.get('expiry')
            expiry_dt = datetime.fromtimestamp(expiry_ts) if expiry_ts else None
            return value, expiry_dt
    return None, None

# 检查是否需要更新cookie
def should_update_cookie(new_value, old_value, expiry_dt, days_threshold=3):
    if new_value is None:
        return False
    if new_value != old_value:
        return True
    if expiry_dt:
        remaining = (expiry_dt - datetime.now()).total_seconds()
        if remaining < days_threshold * 24 * 3600:
            return True
    return False

# 更新cookie到secrets
def update_github_secret(secret_name, new_value):
    if not new_value:
        print(f"⚠️ 跳过更新 {secret_name}：新值为空")
        return False
    masked = new_value[:4] + "..." + new_value[-4:] if len(new_value) > 8 else "***"
    print(f"🔄 更新 Secret: {secret_name} (新值: {masked})")
    try:
        env = os.environ.copy()
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN
        proc = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", new_value],
            capture_output=True, text=True, timeout=30, check=False,
            env=env
        )
        if proc.returncode == 0:
            return True
        else:
            print(f"❌ 更新失败: {proc.stderr.strip()}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

# 发送tg通知
def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")

# 通知格式
def format_notification(status: str, extra: str = "", error: str = "", expiry_date: str = "") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****' 
    
    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        f"{status}",
        f"👤 登录账户: {masked_email}",
    ]
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"⏱️ 登录时间: {now}")
    return "\n".join(lines)

# 等待Turnstile验证通过（检测页面特征）
def wait_for_turnstile_pass(sb, timeout=30):
    start = time.time()
    cf_indicators = ["verify you are human", "确认你是真人", "troubleshoot", "just a moment"]
    while time.time() - start < timeout:
        page_lower = sb.get_page_source().lower()
        if not any(x in page_lower for x in cf_indicators):
            print("✅ Turnstile 验证已通过")
            sb.save_screenshot("turnstile_passed.png")
            return True
        sb.sleep(1)
    print("   ❌ Turnstile 验证超时未通过")
    return False

# 检查页面是否被限流
def is_page_blocked(sb) -> bool:
    page_source = sb.get_page_source()
    if "Error 1015" in page_source or "rate limited" in page_source.lower():
        return True
    return False

# 获取当前出口ip
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()

# 时间格式化
def format_countdown(countdown_str: str) -> str:
    try:
        h, m, _ = countdown_str.split(':')
        h = int(h)
        m = int(m)
        if h > 0:
            return f"{h}h{m}min"
        else:
            return f"{m}min"
    except:
        return countdown_str

# 获取过期日期
def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",   # Expires 2026/07/07
        r"[Ee]xpires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",   # Expires 07/07/2026 (DD/MM/YYYY)
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew",        # 2026/07/07 - renew
        r"(\d{2}/\d{2}/\d{4})\s*[\-–]\s*renew",        # 07/07/2026 - renew
    ]
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            date_str = match.group(1)
            # 如果是 DD/MM/YYYY 格式，转换为 YYYY/MM/DD
            if len(date_str.split('/')[-1]) == 4:  # 最后一部分是4位年份
                parts = date_str.split('/')
                if len(parts[0]) == 2:  # 第一部分是2位，说明是 DD/MM/YYYY
                    return f"{parts[2]}/{parts[1]}/{parts[0]}"
            return date_str
    return None

# 主流程
def main():
    print("#" * 25)
    print("   Bot-hosting 自动续期")
    print("#" * 25)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true" 

    sb_kwargs = {"uc": True, "headless": HEADLESS}

    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 未使用代理，直连访问")

    with SB(**sb_kwargs) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"⚠️ 获取出口 IP 失败: {e}")

        print("🚀 启动浏览器...")
        sb.open("https://bot-hosting.net/")
        sb.wait_for_ready_state_complete()
        sb.sleep(2)

        print("📝 注入 Cookie...")
        for name, value in COOKIES.items():
            if value:
                sb.add_cookie({"name": name, "value": value, "domain": "bot-hosting.net"})

        print("🌐 访问 https://bot-hosting.net/a ...")
        sb.open("https://bot-hosting.net/a")
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        current_url = sb.get_current_url()
        current_title = sb.get_title()
        print(f"📝 当前URL: {current_url}, Title: {current_title}")
        if current_title != "Bot-Hosting.net | A Free Host For Discord Bots" or current_url != "https://bot-hosting.net/a":
            print(f"❌ 登录失败，当前标题: {current_title}")
            send_telegram_message(format_notification("❌ 登录失败", error="Cookie 已失效或页面异常"))
            return
        print(f"✅ 登录成功!")

        print("📝 导航到账单页面...")
        sb.open("https://bot-hosting.net/a/billings")
        sb.wait_for_ready_state_complete()
        print("⏳ 等待页面加载...")
        sb.sleep(8)

        # 限流重试
        max_retries = 3
        retry_count = 0
        while retry_count <= max_retries:
            if is_page_blocked(sb):
                if retry_count < max_retries:
                    print(f"⏳ 检测到限流，等待 30 秒后重试 ({retry_count+1}/{max_retries})...")
                    time.sleep(30)
                    sb.refresh()
                    sb.wait_for_ready_state_complete()
                    sb.sleep(8)
                    retry_count += 1
                    continue
                else:
                    print("❌ 重试后仍触发限流，脚本退出")
                    send_telegram_message(format_notification("❌ 续期失败", error="IP 被限流 (Error 1015)"))
                    return
            else:
                break

        # 提取当前到期日期
        page_source = sb.get_page_source()
        current_expiry = extract_expiry_date(page_source)
        if current_expiry:
            print(f"📅 当前到期日期: {current_expiry}")
        else:
            print("⚠️ 未能提取当前到期日期")

        # 先找外部续期按钮
        outer_renew_selector = None
        countdown_text = None
        possible_selectors = [
            'button:contains("Renew")',
            'a:contains("Renew")',
            '[class*="renew"]',
            '[class*="Renew"]',
        ]

        for selector in possible_selectors:
            try:
                if sb.is_element_visible(selector):
                    button_text = sb.get_text(selector)
                    if "Renew in" in button_text:
                        match = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", button_text)
                        if match:
                            countdown_text = match.group(1)
                        # 找到倒计时，跳出循环
                        break
                    elif "Renew" in button_text and "in" not in button_text.lower():
                        outer_renew_selector = selector
                        print(f"✅ 期按钮可用: '{button_text}'")
                        break
            except Exception as e:
                pass

        # 如果外部按钮存在且可用
        if outer_renew_selector:
            print("🔄 点击续期按钮...")
            try:
                sb.click(outer_renew_selector)
                sb.sleep(3)  # 等待模态框加载
            except Exception as e:
                print(f"❌ 点击外部按钮失败: {e}")
                send_telegram_message(format_notification("❌ 续期失败", error="点击外部续期按钮出错"))
                return

            # 处理弹窗中的 Turnstile 
            print("🔒 检测弹窗中的 Turnstile 验证...")
            turnstile_passed = False
            # 因为 Turnstile 可能在 iframe 内，使用 uc_gui_click_captcha 会自动处理
            for attempt in range(1, 4):
                try:
                    # 尝试点击 Turnstile（如果已经出现）
                    sb.uc_gui_click_captcha()
                    time.sleep(8)
                except Exception as e:
                    print(f"⚠️ 点击 Turnstile 出错: {e}")

                if wait_for_turnstile_pass(sb, timeout=20):
                    turnstile_passed = True
                    # print(f"✅ Turnstile 验证通过（第 {attempt} 次尝试）")
                    break
                else:
                    print(f"⏳ 第 {attempt} 次未通过，重试点击...")
                    # 可尝试重新触发，但直接循环

            if not turnstile_passed:
                print("❌ Turnstile 验证最终未通过，脚本退出")
                send_telegram_message(format_notification("❌ 续期失败", error="Turnstile 验证未通过"))
                return

            # 点击弹窗内的续期按钮
            modal_renew_selectors = [
                'button:contains("Renew for 4 days")',
                'button:contains("Renew")',
                'button:contains("续期")',
                '[class*="renew"]',  # 兜底
            ]
            modal_renew_clicked = False
            for sel in modal_renew_selectors:
                try:
                    # 等待按钮可见且可点击（可能验证后启用）
                    if sb.is_element_visible(sel, timeout=5):
                        print(f"🔄 点击弹窗内续期按钮: {sel}")
                        sb.click(sel)
                        modal_renew_clicked = True
                        break
                except Exception as e:
                    continue
            if not modal_renew_clicked:
                print("⚠️ 未找到弹窗内续期按钮，可能已经自动续期或页面结构变化")
                # 依然继续，尝试刷新获取状态

            #  续期后处理 
            print("⏳ 等待续期处理完成...")
            sb.sleep(6)

            # 提取新的到期日期和倒计时
            new_page_text = sb.get_page_source()
            new_expiry = extract_expiry_date(new_page_text)
            new_match = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", new_page_text)
            if new_match:
                new_countdown = new_match.group(1)
                print(f"✅ 续期成功！新的倒计时: {new_countdown}")
                if new_expiry:
                    print(f"📅 新的到期日期: {new_expiry}")
                send_telegram_message(
                    format_notification(
                        "✅ 续期成功",
                        extra=f"⏱️ 可续期时间: {format_countdown(new_countdown)}后",
                        expiry_date=new_expiry or "（未获取到）"
                    )
                )
            else:
                # 可能续期成功，但倒计时消失（刚续期完没有倒计时？）
                if new_expiry:
                    print(f"✅ 续期成功，到期日期: {new_expiry}")
                    send_telegram_message(
                        format_notification(
                            "✅ 续期成功",
                            extra="请登录后台查看最新状态",
                            expiry_date=new_expiry
                        )
                    )
                else:
                    print("⚠️ 续期状态未知，请手动检查")
                    send_telegram_message(
                        format_notification(
                            "⚠️ 续期结果未知",
                            extra="请登录后台检查",
                            expiry_date=current_expiry or "（未获取到）"
                        )
                    )

        else:
            # 没有可用的外部续期按钮
            if countdown_text:
                friendly = format_countdown(countdown_text)
                print(f"⏳ 未到续期时间，倒计时: {countdown_text} ({friendly})")
                send_telegram_message(
                    format_notification(
                        "⏳ 未到续期时间",
                        extra=f"⏱️ 可续期时间: {friendly}后",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )
            else:
                print("ℹ️ 未找到续期按钮或倒计时，状态未知")
                send_telegram_message(
                    format_notification(
                        "ℹ️ 无需续期",
                        extra="当前状态未知，请手动检查",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )

        # ------------------- 更新 SESSION_TOKEN（不变） -------------------
        print("🔄 检查 SESSION_TOKEN 是否需要更新")
        new_token, token_expiry = get_cookie_info(sb, "session_token")
        old_token = SESSION_TOKEN

        if should_update_cookie(new_token, old_token, token_expiry):
            print("🔄 SESSION_TOKEN 需要更新")
            if GH_TOKEN:
                if update_github_secret("SESSION_TOKEN", new_token):
                    print("✅ SESSION_TOKEN 更新成功")
                else:
                    print("⚠️ 更新失败，请检查 GH_TOKEN 权限")
            else:
                print("⚠️ 未设置 GH_TOKEN，无法自动更新")
                print(f"📋 请手动设置 SESSION_TOKEN = {new_token[:4]}...{new_token[-4:]}")
        else:
            print("✅ SESSION_TOKEN 无需更新")
        
        print("🏁 脚本执行完毕")

if __name__ == "__main__":
    main()
