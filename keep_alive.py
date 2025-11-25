"""
Keep-Alive Server for Render Free Tier

This module runs a simple Flask web server in a background thread.
The server responds to HTTP requests, which allows external services
like UptimeRobot to ping the server and keep it alive.

Render's free tier spins down services after 15 minutes of inactivity.
By having an external service ping this endpoint every 5 minutes,
the bot stays active 24/7.
"""

from flask import Flask
from threading import Thread
import logging

# Suppress Flask's default logging to keep console clean
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

@app.route('/')
def home():
    """Root endpoint - confirms bot is running."""
    return "🤖 YouTube Telegram Bot is alive and running!"

@app.route('/health')
def health():
    """Health check endpoint for monitoring services."""
    return {
        "status": "healthy",
        "service": "youtube-telegram-bot",
        "message": "Bot is operational"
    }

@app.route('/ping')
def ping():
    """Simple ping endpoint."""
    return "pong"

def run():
    """Run the Flask server."""
    # Use 0.0.0.0 to allow external connections
    # Port 8080 is commonly used, but Render will assign $PORT
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)

def keep_alive():
    """
    Start the keep-alive server in a background thread.
    
    This allows the main bot to run while the web server
    handles incoming HTTP requests to keep the service alive.
    """
    server_thread = Thread(target=run, daemon=True)
    server_thread.start()
    print(f"✅ Keep-alive server started")
    return server_thread
