"""
Keep-Alive Server for Render Free Tier
"""

from flask import Flask, jsonify
from threading import Thread
import logging
import os

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 YouTube Telegram Bot is alive!"

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "youtube-telegram-bot"
    })

@app.route('/ping')
def ping():
    return "pong"

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)

def keep_alive():
    server_thread = Thread(target=run, daemon=True)
    server_thread.start()
    print(f"✅ Keep-alive server started on port {os.environ.get('PORT', 8080)}")
    return server_thread
