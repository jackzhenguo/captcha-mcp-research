import time
import csv
import pytesseract
from PIL import Image, ImageDraw
from io import BytesIO
import requests
from datetime import datetime
from flask import Flask, request, send_file, render_template_string
from threading import Thread
from playwright.sync_api import sync_playwright, Page, TimeoutError, Error

"""
CAPTCHA Bot Demo (All-in-One Script)

This script demonstrates:
1. A simple Flask server that serves a CAPTCHA web page with a 4-digit image.
2. A Playwright-based bot that visits the page, reads the CAPTCHA image using OCR (pytesseract),
   fills the form, submits it, and records whether it passed or failed.

Run this script directly, and it will:
- Launch the Flask server on localhost:5000
- Open a browser window 3 times, solve CAPTCHA, and log results to `captcha_logs_bot.csv`
"""
# ========== Part 1: Flask CAPTCHA Server ==========
app = Flask(__name__)
CAPTCHA_CODE = ""

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head><title>CAPTCHA Page</title></head>
<body>
    <h2>Please solve the CAPTCHA</h2>
    <form method="POST">
        <img src="/captcha.png" alt="captcha"><br><br>
        <input type="text" name="captcha_response" required>
        <button type="submit">Submit</button>
    </form>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_PAGE)


@app.route("/", methods=["POST"])
def submit():
    user_input = request.form.get("captcha_response", "")
    if user_input == CAPTCHA_CODE:
        return "Success!"
    return "Failed!"


@app.route("/captcha.png")
def captcha():
    global CAPTCHA_CODE
    CAPTCHA_CODE = str(datetime.now().microsecond % 10000).zfill(4)
    img = Image.new('RGB', (100, 40), color='white')
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), CAPTCHA_CODE, fill='black')
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype='image/png')


def run_server():
    app.run(port=5000, debug=False)


# ========== Part 2: Playwright Bot ==========
URL = "http://127.0.0.1:5000/"
LOG_FILE = "captcha_logs_bot.csv"


def solve_captcha_from_image(page: Page) -> str:
    try:
        page.wait_for_selector("img", timeout=5000)
        img_src = page.locator("img").get_attribute("src")
        if not img_src:
            print("CAPTCHA image src not found.")
            return ""
        captcha_url = img_src if img_src.startswith("http") else URL.rstrip("/") + img_src
        response = requests.get(captcha_url, timeout=3)
        img = Image.open(BytesIO(response.content))
        text = pytesseract.image_to_string(img, config='--psm 7 digits')
        return ''.join(filter(str.isdigit, text))
    except Exception as e:
        print(f"CAPTCHA OCR Error: {e}")
        return ""


def run_attempt(page: Page) -> dict:
    try:
        response = page.goto(URL, wait_until="domcontentloaded", timeout=8000)
        if response is None or response.status >= 400:
            raise Exception("Page load failed.")
        page.wait_for_selector("img", timeout=5000)
        captcha_text = solve_captcha_from_image(page)
        if not captcha_text or len(captcha_text) < 4:
            return {"timestamp": datetime.now().isoformat(), "captcha": captcha_text,
                "result": "OCR_FAIL", "solve_time": 0}
        page.fill("input[name='captcha_response']", captcha_text)
        start_submit = time.time()
        page.click("button[type='submit']")
        time.sleep(0.8)
        result = "PASS" if "Success" in page.locator("body").inner_text() else "FAIL"
        solve_time = round(time.time() - start_submit, 2)
        return {"timestamp": datetime.now().isoformat(), "captcha": captcha_text, "result": result,
            "solve_time": solve_time}
    except TimeoutError:
        return {"timestamp": datetime.now().isoformat(), "captcha": "", "result": "TIMEOUT",
            "solve_time": 0}
    except Error as e:
        return {"timestamp": datetime.now().isoformat(), "captcha": "", "result": "PAGE_ERROR",
            "solve_time": 0}
    except Exception as e:
        print(f"Error: {e}")
        return {"timestamp": datetime.now().isoformat(), "captcha": "", "result": "ERROR",
            "solve_time": 0}


def run_multiple(n: int = 3):
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        for i in range(n):
            print(f"\n--- Attempt #{i + 1} ---")
            result = run_attempt(page)
            print(
                f"Captcha='{result['captcha']}' → {result['result']} (⏱️ {result['solve_time']}s)")
            results.append(result)
            time.sleep(0.5)
        browser.close()
    with open(LOG_FILE, "w", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "captcha", "result", "solve_time"])
        writer.writeheader()
        writer.writerows(results)
    passed = sum(1 for r in results if r["result"] == "PASS")
    print(f"\n✅ Completed {n} attempts — Passed: {passed}, Failed: {n - passed}")


# ========== Part 3: Entry ==========
if __name__ == "__main__":
    # Start Flask server in background thread
    flask_thread = Thread(target=run_server, daemon=True)
    flask_thread.start()

    # Wait a bit for Flask to be ready
    time.sleep(1.5)

    # Run the CAPTCHA bot
    run_multiple(100)
