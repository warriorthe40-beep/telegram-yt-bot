from dotenv import load_dotenv
load_dotenv()

import logging
import os
import re
import asyncio
import tempfile
import threading
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp
from flask import Flask, request

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get token
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN environment variable set!")

YOUTUBE_URL_REGEX = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})'

# --- Utility Functions ---
async def get_video_info(url: str):
    """Uses yt-dlp to extract video info without downloading."""
    ydl_opts = {'quiet': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            return info
    except Exception as e:
        logger.error(f"Error extracting info for {url}: {e}")
        return None

async def download_media(url: str, video_id: str, format_type: str, temp_dir: str):
    """Downloads and processes the video/audio. Returns the path to the final file."""
    base_filename = os.path.join(temp_dir, video_id)
    
    if format_type == 'audio':
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': f"{base_filename}.%(ext)s",
            'quiet': True,
        }
        final_path = f"{base_filename}.mp3"
    else:  # video
        ydl_opts = {
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best[height<=720]',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'outtmpl': f"{base_filename}.%(ext)s",
            'quiet': True,
        }
        final_path = f"{base_filename}.mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await asyncio.to_thread(ydl.download, [url])
        
        if os.path.exists(final_path):
            return final_path
        else:
            # Sometimes the file has a slightly different extension
            for f in os.listdir(temp_dir):
                if f.startswith(video_id) and f.endswith(('.mp3', '.mp4')):
                    os.rename(os.path.join(temp_dir, f), final_path)
                    return final_path
            logger.error(f"Expected file {final_path} not found after download.")
            return None
    except Exception as e:
        logger.error(f"Error downloading {url} as {format_type}: {e}")
        return None

# --- Bot Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text(
        "Hi! Send me a YouTube link and I'll help you download it as audio or video."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages to find YouTube links."""
    message_text = update.message.text
    match = re.search(YOUTUBE_URL_REGEX, message_text)
    
    if match:
        video_id = match.group(1)
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        if not context.user_data:
            context.user_data = {}
        context.user_data[video_id] = url
        
        logger.info(f"Found YouTube link: {url}")
        
        keyboard = [
            [
                InlineKeyboardButton("Download Audio (MP3)", callback_data=f"a:{video_id}"),
                InlineKeyboardButton("Download Video (MP4)", callback_data=f"v:{video_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("What format would you like?", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Please send a valid YouTube link.")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parses the CallbackQuery and initiates the download."""
    query = update.callback_query
    await query.answer()

    try:
        data_type, video_id = query.data.split(":", 1)
    except ValueError:
        await query.edit_message_text("Error: Invalid callback data. Please try sending the link again.")
        return

    url = context.user_data.get(video_id)
    if not url:
        await query.edit_message_text("Error: I've forgotten that link. Please send it again.")
        return

    format_type = "audio" if data_type == "a" else "video"
    logger.info(f"User requested {format_type} for {video_id}")
    
    await query.edit_message_text(f"Processing... this may take a moment. ⏳")

    try:
        info = await get_video_info(url)
        if not info:
            await query.edit_message_text("Error: Could not get video information.")
            return

        title = info.get('title', 'Downloaded Media')
        duration = int(info.get('duration', 0))
        
        with tempfile.TemporaryDirectory() as temp_dir:
            final_path = await download_media(url, video_id, format_type, temp_dir)
            
            if not final_path:
                await query.edit_message_text("Error: Failed to download or process the file.")
                return

            file_size = os.path.getsize(final_path)
            if file_size > 50 * 1024 * 1024:
                logger.warning(f"File {final_path} is too large: {file_size} bytes")
                await query.edit_message_text(
                    "Error: The resulting file is over 50MB and cannot be sent. "
                    "Try a shorter video."
                )
                return

            await query.edit_message_text(f"Uploading {format_type}...")
            
            # Use context managers for file handles
            if format_type == 'audio':
                with open(final_path, 'rb') as f:
                    await update.effective_message.reply_audio(
                        audio=f,
                        title=title,
                        duration=duration
                    )
            else:
                width = info.get('width', 0)
                height = info.get('height', 0)
                with open(final_path, 'rb') as f:
                    await update.effective_message.reply_video(
                        video=f,
                        title=title,
                        duration=duration,
                        width=width,
                        height=height
                    )
            
            await query.delete_message()

    except Exception as e:
        logger.error(f"Main processing error for {url}: {e}", exc_info=True)
        try:
            await query.edit_message_text(f"An unexpected error occurred. Please try again.")
        except:
            pass

# --- Initialize Telegram Bot Application ---
logger.info("Building Telegram bot application...")
ptb_app = Application.builder().token(TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
ptb_app.add_handler(CallbackQueryHandler(button_click))

# --- Flask App ---
app = Flask(__name__)

@app.before_request
def init_bot():
    """Initialize bot before first request"""
    if not hasattr(app, 'bot_initialized'):
        logger.info("Initializing bot on first request...")
        try:
            # Run initialization in the app's own event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(ptb_app.initialize())
            app.bot_initialized = True
            logger.info("Bot initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}", exc_info=True)
            app.bot_initialized = False

@app.route("/")
def index():
    return "Hello, I am your bot and I am running!"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    """Handle incoming webhook updates from Telegram"""
    try:
        logger.info("Webhook endpoint hit")
        
        # Get the update
        update_json = request.get_json(force=True)
        if not update_json:
            logger.error("No JSON data in webhook request")
            return "no data", 400
        
        logger.info(f"Received update: {update_json.get('update_id', 'unknown')}")
        
        # Process the update
        update = Update.de_json(update_json, ptb_app.bot)
        
        # Create a new event loop for this request
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Process the update synchronously in this loop
            loop.run_until_complete(ptb_app.process_update(update))
            logger.info(f"Update {update.update_id} processed successfully")
        finally:
            loop.close()
        
        return "ok", 200
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        # Return 200 anyway to prevent Telegram from retrying
        return "error logged", 200

@app.route("/set_webhook")
def set_webhook():
    """Set the webhook URL for Telegram to send updates to"""
    try:
        # Use the provided Render URL or fallback to environment variable
        render_url = "https://delight-yt-bot.onrender.com"
        
        webhook_url = f"{render_url}/{TOKEN}"
        telegram_api_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
        
        logger.info(f"Setting webhook to: {webhook_url}")
        
        response = requests.post(
            telegram_api_url,
            json={
                'url': webhook_url,
                'allowed_updates': ['message', 'callback_query']
            },
            timeout=10
        )
        response.raise_for_status()
        
        response_json = response.json()
        if response_json.get("ok"):
            logger.info(f"Webhook set successfully to {webhook_url}")
            return f"Webhook set successfully to {webhook_url}", 200
        else:
            error_msg = response_json.get('description', 'Unknown error')
            logger.error(f"Failed to set webhook: {error_msg}")
            return f"Error: Failed to set webhook. {error_msg}", 500
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error setting webhook via requests: {e}", exc_info=True)
        return f"Internal server error setting webhook: {str(e)}", 500
    except Exception as e:
        logger.error(f"General error in set_webhook: {e}", exc_info=True)
        return f"Internal server error: {str(e)}", 500

if __name__ == "__main__":
    # For local testing only
    app.run(host="0.0.0.0", port=5000, debug=True)
