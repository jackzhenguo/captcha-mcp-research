# click_recaptcha_diag.py
from playwright.sync_api import sync_playwright
import re, time

URL = "https://captcha-mcp-vercel-client.vercel.app"

with sync_playwright() as pw:
    # Use real Chrome & a persistent profile (reduces bot fingerprint)
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir="/tmp/pw-profile",
        channel="chrome",
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    page = ctx.new_page()

    # helpful logs
    page.on("console", lambda m: print("console>", m.type, m.text))
    page.on("response", lambda r: print("http>", r.status, r.url))

    # hook BEFORE navigation: wrap grecaptcha.render so we can capture the token
    page.add_init_script(r"""
      (() => {
        window.__recaptchaToken = null;
        const wrap = (cb) => (t) => { try { window.__recaptchaToken = t; } catch(e) {} try{ cb && cb(t); }catch(e){} };
        Object.defineProperty(window, "grecaptcha", {
          configurable: true,
          set(v) {
            if (v && typeof v.render === "function" && !v.__wrapped) {
              const orig = v.render.bind(v);
              v.render = (container, params = {}, ...rest) => {
                if (params && typeof params.callback === "function") params.callback = wrap(params.callback);
                return orig(container, params, ...rest);
              };
              v.__wrapped = true;
            }
            this.__gc = v;
          },
          get() { return this.__gc; }
        });
      })();
    """)

    page.goto(URL, wait_until="domcontentloaded")

    # wait until the page enables the button (means recaptchaReady ran)
    page.wait_for_selector("#verifyBtn:not([disabled])", timeout=20000)

    # trigger the flow
    try:
        page.evaluate("() => window.triggerVerify && window.triggerVerify()")
    except Exception:
        pass
    if page.locator("#verifyBtn").is_enabled():
        page.click("#verifyBtn")
    else:
        page.keyboard.press("Enter")

    # poll for a token OR detect the challenge frame
    token = None
    for i in range(45):
        token = page.evaluate("() => window.__recaptchaToken || null")
        if token:
            break
        # If the challenge UI (api2/bframe) appears, automation won't get a token
        frames = [f.url for f in page.frames]
        if any("recaptcha/api2/bframe" in u for u in frames):
            print("⚠️  reCAPTCHA challenge frame detected — a human solve is required; no token will be issued automatically.")
            break
        time.sleep(1)

    verdict = (page.locator("#verdict").text_content() or "").strip()
    if token:
        print("✅ Client token:", (token[:28] + "…") if len(token) > 28 else token)
    else:
        print("⏱️  No token after polling. Verdict:", verdict or "—")

    page.screenshot(path="recaptcha_result.png", full_page=True)
    print("Saved screenshot: recaptcha_result.png")

    ctx.close()
