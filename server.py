import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
import requests

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _value = _line.split("=", 1)
        os.environ.setdefault(_key.strip(), _value.strip())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
#-----------------------------------------------------------------------

API_SECRET = os.environ.get("API_SECRET", "غيّر-هذا-السر-لاحقاً")

# رابط Discord Webhook - احصل عليه من إعدادات القناة > Integrations > Webhooks
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# قائمة أسماء حزم التطبيقات المحظورة (package name)
BLOCKED_PACKAGES = {
    "com.instagram.android": "انستقرام",
    "com.snapchat.android": "سناب شات",
    "com.zhiliaoapp.musically": "تيك توك",
    "com.facebook.katana": "فيسبوك",
    "com.facebook.orca": "فيسبوك ماسنجر",
    "com.twitter.android": "تويتر/X",
    "com.discord": "ديسكورد",
    "com.whatsapp": "واتساب",  # احذفها لو تبي تسمح بالواتساب
}

DB_PATH = os.path.join(os.path.dirname(__file__), "events.db")


# ---------------------------------------------------------------------------
# قاعدة البيانات
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_name TEXT NOT NULL,
            app_label TEXT,
            event_type TEXT NOT NULL,
            blocked INTEGER NOT NULL DEFAULT 0,
            device_id TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def save_event(package_name, app_label, event_type, blocked, device_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO events (package_name, app_label, event_type, blocked, device_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (package_name, app_label, event_type, int(blocked), device_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# التنبيهات عبر Discord Webhook
# ---------------------------------------------------------------------------

def send_discord_alert(text: str):
    if not DISCORD_WEBHOOK_URL:
        logger.warning("Discord webhook غير مُعد - تخطي الإرسال. الرسالة: %s", text)
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": text}, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("فشل إرسال تنبيه Discord: %s", exc)
        return False


# ---------------------------------------------------------------------------
# المسارات (Endpoints)
# ---------------------------------------------------------------------------

@app.before_request
def check_auth():
    # كل الطلبات لازم تحمل المفتاح السري في الهيدر، إلا فحص الصحة
    if request.path == "/health":
        return
    provided = request.headers.get("X-API-Secret", "")
    if provided != API_SECRET:
        return jsonify({"error": "unauthorized"}), 401


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/app_event", methods=["POST"])
def app_event():
    """
    التطبيق على الهاتف يرسل هنا كل ما يكتشف تثبيت أو تشغيل تطبيق.
    Body المتوقع (JSON):
    {
        "package_name": "com.instagram.android",
        "app_label": "Instagram",
        "event_type": "INSTALLED",   // أو "LAUNCH_ATTEMPT"
        "device_id": "اختياري - معرف الجهاز"
    }
    """
    data = request.get_json(silent=True) or {}
    package_name = data.get("package_name", "")
    app_label = data.get("app_label", package_name)
    event_type = data.get("event_type", "UNKNOWN")
    device_id = data.get("device_id", "unknown")

    if not package_name:
        return jsonify({"error": "package_name مطلوب"}), 400

    is_blocked = package_name in BLOCKED_PACKAGES
    save_event(package_name, app_label, event_type, is_blocked, device_id)

    if is_blocked:
        friendly_name = BLOCKED_PACKAGES[package_name]
        message = (
            f"⚠️ تنبيه\n"
            f"تطبيق محظور: {friendly_name} ({package_name})\n"
            f"الحدث: {event_type}\n"
            f"الوقت: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )
        send_discord_alert(message)
        logger.info("تم رصد تطبيق محظور: %s", package_name)

    return jsonify({"status": "ok", "blocked": is_blocked})


@app.route("/events", methods=["GET"])
def list_events():
    """عرض آخر الأحداث المسجلة - مفيد للوحة تحكم أو فحص سريع."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/blocked_packages", methods=["GET"])
def blocked_packages():
    """التطبيق على الهاتف يقدر يسحب القائمة المحظورة من هنا (بدل ما تكون مكتوبة بداخله)."""
    return jsonify(BLOCKED_PACKAGES)


# تهيئة قاعدة البيانات عند استيراد الملف - تشتغل سواء عبر "python server.py"
# مباشرة أو عبر gunicorn (مثل ما يحصل على Render). لو تركناها فقط داخل
# if __name__ == "__main__"، gunicorn ما يستدعيها أبداً والجدول ما يتكون.
init_db()

if __name__ == "__main__":
    logger.info("الخادم يبدأ على المنفذ 5000 ...")
    app.run(host="0.0.0.0", port=5000, debug=True)
