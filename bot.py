# This is the corrected bot.py file.
# It fixes the "RuntimeError: This application was not initialized" error.

from dotenv import load_dotenv
load_dotenv() # Load .env file, though Render uses its own env vars

import logging
import os
import re
import asyncio
import tempfile
import shutil
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get Token from environment
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN environment variable set!")

# Regex to find YouTube video IDs
YOUTUBE_URL_REGEX = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})'

# --- Utility Functions (Async) ---

async def get_video_info(url: str):
    """
    Uses yt-dlp to extract video info without downloading.
    This is run in a separate thread to avoid blocking asyncio.
    """
    ydl_opts = {'quiet': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Run the blocking ydl.extract_info in a separate thread
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)
            return info
    except Exception as e:
        logger.error(f"Error extracting info for {url}: {e}")
        return None

async def download_media(url: str, video_id: str, format_type: str, temp_dir: str):
    """
    Downloads and processes the video/audio in a separate thread.
    Returns the path to the final file.
    """
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
            # Run the blocking ydl.download in a separate thread
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

# --- Bot Command Handlers (Async) ---

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
            
            if format_type == 'audio':
                await update.effective_message.reply_audio(
                    audio=open(final_path, 'rb'),
                    title=title,
                    duration=duration
                )
            else:
                width = info.get('width', 0)
                height = info.get('height', 0)
                await update.effective_message.reply_video(
                    video=open(final_path, 'rb'),
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
            pass # Message might have been deleted

# --- Webhook Setup ---

# Set up the PTB application
ptb_app = Application.builder().token(TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
ptb_app.add_handler(CallbackQueryHandler(button_click))

# *** THIS IS THE FIX ***
# Initialize the application asynchronously *before* setting up the web server
logger.info("Initializing PTB Application...")
asyncio.run(ptb_app.initialize())
logger.info("PTB Application Initialized.")


# Set up the Flask app (this is the web server)
app = Flask(__name__)

@app.route("/")
def index():
    """A simple health check endpoint for Render."""
    return "Hello, I am your bot and I am running!"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    """
    This is the main webhook endpoint.
    It is NOT async, but it calls an async function.
    """
    update_json = request.get_json(force=True)
    update = Update.de_json(update_json, ptb_app.bot)
    logger.info(f"Received update {update.update_id}")
    
    # Run the async process_update function in a blocking way
    asyncio.run(ptb_app.process_update(update))
    
    return "ok", 200

@app.route("/set_webhook")
def set_webhook():
    """
    This is the one-time endpoint to set the webhook.
    It is NOT async, but it calls an async function.
    """
    # RENDER_EXTERNAL_URL is automatically set by Render
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        logger.error("RENDER_EXTERNAL_URL environment variable not found.")
        return "Error: RENDER_EXTERNAL_URL environment variable not found.", 500

    webhook_url = f"{render_url}/{TOKEN}"
    
    # Run the async set_webhook function in a blocking way
    set_ok = asyncio.run(ptb_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES))
    
    if set_ok:
        logger.info(f"Webhook set successfully to {webhook_url}")
        return f"Webhook set successfully to {webhook_url}", 200
    else:
        logger.error("Failed to set webhook.")
        return "Error: Failed to set webhook.", 500

# Note: We do not run `ptb_app.run_polling()`
# Gunicorn will run the Flask `app` object (`gunicorn bot:app`)

