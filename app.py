#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, requests, subprocess, io
from datetime import datetime
from seleniumbase import SB

# 环境变量配置(可以直接私库在双引号里填写)
EMAIL         = os.environ.get("EMAIL") or "xxxxx@gmail.com"   # 邮箱,只用于通知使用，可随意填写
SESSION_TOKEN = os.environ.get("SESSION_TOKEN") or ""          # session token，必须填写
GH_TOKEN      = os.environ.get("GH_TOKEN") or ""               # GitHub PAT token,用于自动更新session token,必须填写
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""             # TG chat id,不填写不通知，需和bot token一起填写生效
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""           # TG bot token
GITHUB_REPO   = os.environ.get("GITHUB_REPOSITORY") or ""      # GitHub 仓库名（Actions 自动注入）
CURRENT_IP    = ""                                                # 当前出口IP（运行时获取）

if not SESSION_TOKEN :
    print("ℹ️ 未配置 SESSION_TOKEN,脚本终止。")
    sys.exit(1)

# 构造cookie
USER_ID   = os.environ.get("USER_ID") or "206230573"   # bot-hosting user_id，用于辅助登录态判定
USERNAME  = os.environ.get("USERNAME") or "btpp03"      # bot-hosting 用户名，仅通知展示
COOKIES = {
    "session_token": SESSION_TOKEN,
    "login": "true",
    "theme": "system",
    "user_id": USER_ID,
    "username": USERNAME,
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

# 发送tg文字通知
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

# 发送tg截图（带文字说明）
def send_telegram_photo(caption: str, photo_path: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过截图通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": caption}, files=files, timeout=15)
        print("✅ Telegram 截图已发送")
    except Exception as e:
        print(f"❌ Telegram 截图发送失败: {e}")

# 通知格式
def format_notification(status: str, extra: str = "", error: str = "", expiry_date: str = "") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        f"{status}",
        f"👤 登录账户: {EMAIL}",
    ]
    if GITHUB_REPO:
        lines.append(f"📂 仓库: {GITHUB_REPO}")
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    if CURRENT_IP:
        parts = CURRENT_IP.split('.')
        masked_ip = '.'.join(parts[:2] + ['x', 'x']) if len(parts) >= 4 else CURRENT_IP
        lines.append(f"🌐 出口IP: {masked_ip}")
    lines.append(f"⏱️ 执行时间: {now}")
    return "\n".join(lines)

# 读取 Turnstile 实际签发的 token。
# 注意：不能通过主页面里有没有“Verify you are human”来判断；Turnstile 位于
# 跨域 iframe 中，该文字通常不会出现在主页面源码，旧逻辑会把未通过误报为成功。
def get_turnstile_token(sb) -> str:
    """读取主文档中 Cloudflare Turnstile 注入的隐藏 token（原生 API，不用 execute_script）。"""
    selectors = [
        'input[name="cf-turnstile-response"]',
        'textarea[name="cf-turnstile-response"]',
        'input[name="g-recaptcha-response"]',
        'textarea[name="g-recaptcha-response"]',
    ]
    for sel in selectors:
        try:
            if sb.is_element_present(sel):
                val = sb.get_attribute(sel, "value") or ""
                val = val.strip()
                if val:
                    return val
        except Exception:
            continue
    return ""


def wait_for_turnstile_pass(sb, timeout=30):
    """等待 Turnstile 通过：以主文档 token 出现为准，辅以验证文字消失兜底。"""
    start = time.time()
    cf_indicators = ["verify you are human", "确认您是真人", "troubleshoot", "just a moment"]
    while time.time() - start < timeout:
        token = get_turnstile_token(sb)
        if len(token) >= 20:
            print(f"✅ Turnstile 验证已通过（token 长度: {len(token)}）")
            return True
        # 兜底：某些页面不暴露隐藏 token 字段，此时以验证文字是否消失判断
        try:
            page_lower = sb.get_page_source().lower()
            if not any(x in page_lower for x in cf_indicators):
                print("✅ Turnstile 验证已通过（页面验证文字消失）")
                return True
        except Exception:
            pass
        sb.sleep(1)
    print("❌ Turnstile 验证超时：未取得有效 cf-turnstile-response token")
    return False


def get_renew_button_state(sb):
    """返回弹窗续期按钮的存在/禁用状态（XPath 定位，最可靠）。"""
    from selenium.webdriver.common.by import By
    xpaths = [
        '//button[contains(., "Renew for 4 days")]',
        '//button[contains(text(), "Renew for")]',
        "//button[contains(., 'Renew') and contains(., 'days')]",
    ]
    try:
        driver = sb.driver
        # 确保回到主文档：uc_gui_click_captcha 可能把焦点切进 Turnstile iframe
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        for xp in xpaths:
            try:
                els = driver.find_elements(By.XPATH, xp)
            except Exception:
                continue
            if els:
                el = els[0]
                disabled = el.get_attribute("disabled") or el.get_attribute("aria-disabled")
                text = (el.text or "").strip()
                return {
                    "exists": True,
                    "disabled": bool(disabled) or disabled == "true" or disabled == "disabled",
                    "text": text,
                }
    except Exception as e:
        print(f"⚠️ 读取续期按钮状态失败: {e}")
    return {"exists": False, "disabled": True, "text": ""}


def get_page_feedback(sb) -> str:
    """抓取提交后出现的 toast/alert/dialog 文本（原生 API，不用 execute_script）。"""
    selectors = [
        '[role="alert"]', '[role="status"]',
        '[class*="toast"]', '[class*="Toast"]',
        '[class*="alert"]', '[class*="Alert"]',
        '[class*="notification"]', '[class*="Notification"]',
        '[aria-live="assertive"]', '[aria-live="polite"]',
    ]
    texts = []
    for sel in selectors:
        try:
            for el in sb.find_elements(sel):
                t = (el.text or "").strip()
                if t and t not in texts:
                    texts.append(t)
        except Exception:
            continue
    return " | ".join(texts)[:1000]


def dump_dom_for_debug(sb):
    """保存主文档及所有 iframe 的 HTML，用于定位续期按钮真实位置（Selenium 原生）。"""
    from selenium.webdriver.common.by import By
    import os
    try:
        os.makedirs("debug_dom", exist_ok=True)
        driver = sb.driver
        try:
            with open("debug_dom/main.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source or "")
        except Exception as e:
            print(f"⚠️ 主文档 dump 失败: {e}")
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            print(f"🔎 检测到 {len(iframes)} 个 iframe")
            for i, frame in enumerate(iframes):
                try:
                    src = frame.get_attribute("src") or ""
                    driver.switch_to.frame(frame)
                    html = driver.page_source or ""
                    with open(f"debug_dom/iframe_{i}.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    with open(f"debug_dom/iframe_{i}.txt", "w", encoding="utf-8") as f:
                        f.write(f"SRC: {src}\n{html}")
                    print(f"🔎 iframe[{i}] src={src[:120]} size={len(html)}")
                    driver.switch_to.default_content()
                except Exception as e:
                    print(f"⚠️ iframe[{i}] 处理失败: {e}")
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception as e:
            print(f"⚠️ iframe 遍历失败: {e}")
    except Exception as e:
        print(f"⚠️ DOM dump 失败: {e}")

    
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
        r"[Ee]xpires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",   # Expires 07/07/2026 (MM/DD/YYYY)
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew",        # 2026/07/07 - renew
        r"(\d{2}/\d{2}/\d{4})\s*[\-–]\s*renew",        # 07/07/2026 - renew
    ]
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            date_str = match.group(1)
            # 如果是 MM/DD/YYYY 格式，转换为 YYYY/MM/DD
            if len(date_str.split('/')[-1]) == 4:  # 年份长度4
                parts = date_str.split('/')
                if len(parts[0]) == 2:  # 第一部分是2位（月）
                    # 修正：将 MM/DD/YYYY 转为 YYYY/MM/DD
                    return f"{parts[2]}/{parts[0]}/{parts[1]}"
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
            CURRENT_IP = ip
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            CURRENT_IP = "（获取失败）"
            print(f"⚠️ 获取出口 IP 失败: {e}")

        print("🚀 启动浏览器...")
        sb.open("https://bot-hosting.net/")
        sb.wait_for_ready_state_complete()
        sb.sleep(2)

        print("📝 注入 Cookie...")
        for name, value in COOKIES.items():
            if value:
                sb.add_cookie({"name": name, "value": value, "domain": "bot-hosting.net"})

        print("🌐 访问 https://bot-hosting.net/a/billings ...")
        sb.open("https://bot-hosting.net/a/billings")
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        current_url = sb.get_current_url()
        current_title = sb.get_title()
        print(f"📝 当前URL: {current_url}, Title: {current_title}")
        # 登录判定：成功 = 到达账单页(/a/billings) 且未被重定向回登录页
        # 失败 = URL 跳到 /login 或标题仍是未登录的首页标题
        redirected_to_login = "/login" in current_url
        on_guest_home = current_title == "Bot-Hosting.net | A Free Host For Discord Bots" and current_url.rstrip("/") == "https://bot-hosting.net"
        if redirected_to_login or on_guest_home:
            print(f"❌ 登录失败，当前标题: {current_title} | URL: {current_url}")
            send_telegram_message(format_notification("❌ 登录失败", error="Cookie 已失效或页面异常"))
            return
        print(f"✅ 登录成功,当前已到达账单页")

        # 提取当前到期日期
        page_source = sb.get_page_source()
        current_expiry = extract_expiry_date(page_source)
        if current_expiry:
            print(f"📅 当前到期日期: {current_expiry}")
        else:
            print("⚠️ 未能提取当前到期日期")

        # 寻找外部续期按钮
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
                        break
                    elif "Renew" in button_text and "in" not in button_text.lower():
                        outer_renew_selector = selector
                        print(f"✅ 续期按钮可用: '{button_text}'")
                        break
            except Exception as e:
                pass

        # 点击外部续期按钮等待弹窗
        if outer_renew_selector:
            print("🔄 点击外部续期按钮，等待验证窗口...")
            try:
                sb.click(outer_renew_selector)
                sb.sleep(3)  # 等待模态框加载
            except Exception as e:
                print(f"❌ 点击外部按钮失败: {e}")
                send_telegram_message(format_notification("❌ 续期失败", error="点击外部续期按钮出错"))
                return

            # 处理弹窗中的 Turnstile。关键：弃用 sb.uc_gui_click_captcha()（该像素级方法在
            # Xvfb 环境下会导致 Chrome 会话崩溃）。改为直接定位 Turnstile iframe 并点击其
            # 内的复选框（Bot-hosting 的 Turnstile 是"点击即过"的基础型，无图片挑战）。
            from selenium.webdriver.common.by import By as _By

            def _find_turnstile_frame(driver):
                try:
                    for f in driver.find_elements(_By.TAG_NAME, "iframe"):
                        src = (f.get_attribute("src") or "").lower()
                        if "challenges.cloudflare.com" in src or "turnstile" in src:
                            return f
                except Exception:
                    pass
                return None

            def _wait_for_turnstile_frame(driver, timeout=12):
                """轮询等待 Turnstile iframe 出现（Cloudflare 异步注入，可能含 shadow DOM）。"""
                from selenium.webdriver.common.by import By as _By2
                import time as _t
                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        for f in driver.find_elements(_By2.TAG_NAME, "iframe"):
                            src = (f.get_attribute("src") or "").lower()
                            if "challenges.cloudflare.com" in src or "turnstile" in src:
                                return f
                        # 兜底：宽松匹配任意含 turnstile/captcha 的 iframe
                        for f in driver.find_elements(_By2.TAG_NAME, "iframe"):
                            src = (f.get_attribute("src") or "").lower()
                            if "captcha" in src or "challenge" in src or "turnstile" in src:
                                return f
                    except Exception:
                        pass
                    _t.sleep(0.5)
                return None

            def _click_turnstile_checkbox(sb):
                """等 Turnstile iframe 出现后切进并点击复选框，成功返回 True。"""
                try:
                    driver = sb.driver
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
                    frame = _wait_for_turnstile_frame(driver, timeout=12)
                    if not frame:
                        print("  - 等待 12s 后仍未找到 Turnstile iframe")
                        # 兜底：直接读取主文档隐藏 token 字段判断是否已通过
                        try:
                            tok = sb.get_attribute('input[name="cf-turnstile-response"]', "value") or ""
                            if len(tok.strip()) >= 20:
                                print(f"  - 主文档 token 已存在（长度 {len(tok.strip())}），视为已通过")
                                return True
                        except Exception:
                            pass
                        return False
                    print(f"  - 找到 Turnstile iframe: {(frame.get_attribute('src') or '')[:80]}")
                    driver.switch_to.frame(frame)
                    sb.sleep(1.5)
                    clicked = False
                    sel = '[role="checkbox"], input[type="checkbox"], .challenge-container, .ctp-checkbox-label, #challenge-stage input, button'
                    els = driver.find_elements(_By.CSS_SELECTOR, sel)
                    for el in els:
                        try:
                            el.click()
                            clicked = True
                            print(f"  - 已点击 Turnstile iframe 内元素: {el.tag_name}.{el.get_attribute('class') or ''}")
                            break
                        except Exception:
                            continue
                    driver.switch_to.default_content()
                    return clicked
                except Exception as e:
                    print(f"  - iframe 点击异常: {e}")
                    try:
                        sb.driver.switch_to.default_content()
                    except Exception:
                        pass
                    return False

            print("🔒 检测弹窗中的 Turnstile 验证...")
            button_ready = False
            for attempt in range(1, 5):
                state = get_renew_button_state(sb)
                if state.get("exists") and not state.get("disabled"):
                    button_ready = True
                    print(f"✅ 续期按钮已可用: {state.get('text')}")
                    break

                try:
                    print(f"🖱️ 第 {attempt}/4 次尝试点击 Turnstile 复选框...")
                    _click_turnstile_checkbox(sb)
                    sb.sleep(2)
                except Exception as e:
                    print(f"⚠️ 点击 Turnstile 出错: {e}")

                unlocked = False
                for wait_round in range(1, 16):
                    sb.sleep(1)
                    state = get_renew_button_state(sb)
                    if state.get("exists") and not state.get("disabled"):
                        unlocked = True
                        button_ready = True
                        print(f"✅ Turnstile 通过，续期按钮已启用: {state.get('text')}")
                        break
                    if wait_round in (1, 5, 10, 15):
                        print(f"⏳ 等待按钮启用中（{wait_round}/15）: exists={state.get('exists')} disabled={state.get('disabled')}")
                if unlocked:
                    break
                print(f"⏳ 第 {attempt} 次点击后按钮仍未启用，准备重试...")

            if not button_ready:
                sb.save_screenshot("renew_result.png")
                feedback = get_page_feedback(sb)
                print("❌ Turnstile 验证最终未通过，脚本退出")
                send_telegram_photo(
                    format_notification(
                        "❌ 续期失败",
                        error=f"续期按钮始终未解锁（Turnstile 未通过）{': ' + feedback if feedback else ''}"
                    ),
                    "renew_result.png"
                )
                raise RuntimeError("续期按钮未解锁：Turnstile 验证未通过")

            # 点击后先观察弹窗/Toast 和日期变化，避免刷新掉瞬时错误。
            modal_button_clicked = False
            try:
                # 确保回到主文档再点击续期按钮
                try:
                    sb.driver.switch_to.default_content()
                except Exception:
                    pass
                sb.save_screenshot("renew_before_submit.png")
                sb.click('//button[contains(., "Renew for 4 days")]', timeout=8)
                modal_button_clicked = True
                print("✅ 已点击启用状态的续期按钮")
            except Exception as e:
                print(f"❌ 续期按钮点击失败: {e}")

            if not modal_button_clicked:
                sb.save_screenshot("renew_result.png")
                send_telegram_photo(
                    format_notification("❌ 续期失败", error="弹窗续期按钮点击失败"),
                    "renew_result.png"
                )
                raise RuntimeError("弹窗续期按钮点击失败")

            print("⏳ 观察平台即时反馈...")
            renew_confirmed = False
            new_expiry = None
            new_countdown = None
            last_feedback = ""
            for observe_round in range(1, 16):
                sb.sleep(1)
                page_text = sb.get_page_source()
                new_expiry = extract_expiry_date(page_text)
                new_match = re.search(r"Renew in (\d{1,3}:\d{2}:\d{2})", page_text)
                success_hint = re.search(
                    r"renew(?:al|ed)?\s+(?:was\s+)?successful|successfully\s+renewed|renewal\s+complete",
                    page_text, re.I,
                )
                feedback = get_page_feedback(sb)
                if feedback and feedback != last_feedback:
                    last_feedback = feedback
                    print(f"📣 页面反馈: {feedback}")
                if new_expiry and new_expiry != current_expiry:
                    renew_confirmed = True
                    break
                if new_match:
                    new_countdown = new_match.group(1)
                    renew_confirmed = True
                    break
                if success_hint:
                    renew_confirmed = True
                    break

            sb.save_screenshot("renew_after_submit.png")

            # 平台可能异步处理；未即时确认时再刷新账单页核验。
            if not renew_confirmed:
                print("⏳ 未见即时成功结果，刷新账单页继续核验...")
                for check_attempt in range(1, 5):
                    sb.sleep(12)
                    try:
                        sb.refresh_page()
                        sb.sleep(3)
                    except Exception as e:
                        print(f"⚠️ 第 {check_attempt} 次刷新页面失败: {e}")

                    new_page_text = sb.get_page_source()
                    new_expiry = extract_expiry_date(new_page_text)
                    new_match = re.search(r"Renew in (\d{1,3}:\d{2}:\d{2})", new_page_text)
                    success_hint = re.search(
                        r"renew(?:al|ed)?\s+(?:was\s+)?successful|successfully\s+renewed|renewal\s+complete",
                        new_page_text, re.I,
                    )
                    feedback = get_page_feedback(sb)
                    if feedback:
                        last_feedback = feedback

                    print(
                        f"🔎 第 {check_attempt}/4 次检查: "
                        f"到期日期={new_expiry or '未获取'}, "
                        f"倒计时={'有' if new_match else '无'}, "
                        f"成功提示={'有' if success_hint else '无'}, "
                        f"页面反馈={feedback or '无'}"
                    )
                    if new_expiry and new_expiry != current_expiry:
                        renew_confirmed = True
                        break
                    if new_match:
                        new_countdown = new_match.group(1)
                        renew_confirmed = True
                        break
                    if success_hint:
                        renew_confirmed = True
                        break

            sb.save_screenshot("renew_result.png")

            if renew_confirmed:
                if new_expiry and new_expiry != current_expiry:
                    print(f"✅ 续期成功，到期日期已更新为: {new_expiry}")
                    extra = "到期日期已更新"
                elif new_countdown:
                    print(f"✅ 续期成功！新的倒计时: {new_countdown}")
                    extra = f"⏱️ 可续期时间: {format_countdown(new_countdown)}后"
                else:
                    print("✅ 页面已显示续期成功提示")
                    extra = "页面已确认续期成功"

                send_telegram_photo(
                    format_notification(
                        "✅ 续期成功",
                        extra=extra,
                        expiry_date=new_expiry or current_expiry or "（未获取到）"
                    ),
                    "renew_result.png"
                )
            else:
                print("❌ 提交后仍无法确认续期成功")
                if last_feedback:
                    print(f"❌ 最后捕获到的页面反馈: {last_feedback}")
                try:
                    with open("renew_result.html", "w", encoding="utf-8") as f:
                        f.write(sb.get_page_source())
                except Exception as e:
                    print(f"⚠️ 保存最终页面源码失败: {e}")
                send_telegram_photo(
                    format_notification(
                        "❌ 续期未确认",
                        extra="已验证 Turnstile token 和按钮状态，并完成 4 次刷新检查",
                        error=last_feedback or "平台未显示成功或错误提示",
                        expiry_date=new_expiry or current_expiry or "（未获取到）"
                    ),
                    "renew_result.png"
                )
                raise RuntimeError(
                    "续期结果无法确认：到期日期、倒计时和成功提示均未变化"
                    + (f"；页面反馈: {last_feedback}" if last_feedback else "")
                )

        else:
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

        # 更新SESSION_TOKEN 
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
