import os
import io
import csv
import time
import random
from datetime import datetime
from flask import Flask, session, render_template_string, request, send_file

from PIL import Image, ImageDraw, ImageFont

LOG_FILE = os.path.join(os.path.dirname(__file__), "captcha_logs.csv")
print("📁 Writing logs to:", LOG_FILE)

app = Flask(__name__)
app.secret_key = 'replace-this-with-a-random-secret-key'


# Generate a simple numeric CAPTCHA
def generate_captcha_text():
    return str(random.randint(1000, 9999))


# Create a basic CAPTCHA image
def create_captcha_image(text: str) -> Image.Image:
    img = Image.new('RGB', (120, 40), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((10, 10), text, font=font, fill=(0, 0, 0))
    return img


@app.route('/')
def index():
    captcha_text = generate_captcha_text()
    session['captcha_text'] = captcha_text
    session['start_time'] = time.time()
    return render_template_string("""
    <h2>Test CAPTCHA Challenge</h2>
    <img src="{{ url_for('captcha_image') }}" alt="CAPTCHA Image"><br><br>
    <form method="post" action="{{ url_for('submit') }}" id="captchaForm">
        <input type="text" name="captcha_response" placeholder="Enter CAPTCHA" required>
        <input type="hidden" name="mouse_trace" id="mouseTrace">
        <button type="submit">Submit</button>
    </form>
    <script>
    let trace = [];
    document.onmousemove = function(e){
        trace.push([e.pageX, e.pageY, Date.now()]);
        if (trace.length > 100) trace.shift();
    }
    document.getElementById('captchaForm').onsubmit = function(){
        document.getElementById('mouseTrace').value = JSON.stringify(trace);
    }
    </script>
    """)


@app.route('/captcha.png')
def captcha_image():
    text = session.get('captcha_text', '0000')
    img = create_captcha_image(text)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@app.route('/submit', methods=['POST'])
def submit():
    try:
        print("✅ [Submit] Endpoint triggered")

        end_time = time.time()
        start_time = session.get('start_time', end_time)
        solve_time = round(end_time - start_time, 2)

        user_agent = request.headers.get('User-Agent', '')
        ip = request.remote_addr
        mouse_trace = request.form.get('mouse_trace', '')
        captcha_response = request.form.get('captcha_response', '')
        captcha_answer = session.get('captcha_text', '')
        result = (captcha_response == captcha_answer)
        timestamp = datetime.now().isoformat()

        # DEBUG: Print everything before writing
        print(
            f"📌 Writing log → time: {timestamp}, ip: {ip}, ua: {user_agent[:30]}..., time: {solve_time}, result: {result}")
        print(f"🧠 Captcha entered: {captcha_response} | Expected: {captcha_answer}")

        # Try to write log
        with open(LOG_FILE, "a", newline='') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, ip, user_agent, solve_time, result, mouse_trace])
        print("✅ [LOG] Write succeeded")

        return f"<h3>{'✅ Success!' if result else '❌ Failed!'}</h3><a href='/'>Try again</a>"

    except Exception as e:
        print("❌ [ERROR] Exception occurred during /submit:", e)
        return f"<h3>Server error: {str(e)}</h3>"


if __name__ == '__main__':
    # Create CSV header if not exists
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(
                ['timestamp', 'ip', 'user_agent', 'solve_time', 'result', 'mouse_trace'])

    app.run(debug=True)
