import os, time, threading
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()
SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY", "")
SECRET   = os.getenv("RECAPTCHA_SECRET", "")
HOST     = os.getenv("HOST", "127.0.0.1")
PORT     = int(os.getenv("PORT", "8000"))

app = Flask(__name__, template_folder="templates", static_folder=None)

METRICS = dict(total=0, passed=0, failed=0, last_ms=0, last=None, last_ts=0)
LOCK = threading.Lock()


@app.get("/")
def index():
    return '<a href="/recaptcha">Invisible v2 test</a>'

@app.get("/recaptcha")
def recaptcha():
    if not SITE_KEY:
        return "Missing RECAPTCHA_SITE_KEY", 500
    return render_template("recaptcha_invisible.html", site_key=SITE_KEY)


@app.post("/api/verify")
def api_verify():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    t0 = time.time()

    def record(success, reason=None):
        ms = int((time.time() - t0) * 1000)
        with LOCK:
            METRICS["total"] += 1
            METRICS["last_ms"] = ms
            METRICS["last"] = "PASS" if success else ("FAIL" if reason != "missing_token" else "MISSING")
            METRICS["last_ts"] = int(time.time() * 1000)
            if success: METRICS["passed"] += 1
            else: METRICS["failed"] += 1
        return success, ms

    if not token:
        record(False, "missing_token")
        return jsonify({"ok": False, "reason": "missing_token"}), 400

    try:
        r = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": SECRET, "response": token},
            timeout=8,
        )
        result = r.json()
        success = bool(result.get("success"))
        success, _ = record(success)
    except Exception as e:
        record(False, "verify_error")
        return jsonify({"ok": False, "reason": f"verify_error:{e}"}), 502

    verdict_text = "PASS: reCAPTCHA" if success else "FAIL: reCAPTCHA"
    return jsonify({"ok": True, "success": success, "verdict": verdict_text, "raw": result})

@app.get("/metrics")
def metrics():
    with LOCK:
        return jsonify(METRICS)

@app.post("/reset_metrics")
def reset():
    with LOCK:
        METRICS.update(total=0, passed=0, failed=0, last_ms=0, last=None, last_ts=0)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
