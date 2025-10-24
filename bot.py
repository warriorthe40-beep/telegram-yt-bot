from dotenv import load_dotenv
load_dotenv()

import logging
import os
import re
import asyncio
import tempfile
import threading
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from flask import Flask, request
from pytubefix import YouTube
from pytubefix.cli import on_progress

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get tokens
TOKEN = os.environ.get("TELEGRAM_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN environment variable set!")
if not YOUTUBE_API_KEY:
    raise ValueError("No YOUTUBE_API_KEY environment variable set!")

YOUTUBE_URL_REGEX = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})'

# --- Utility Functions ---
async def get_video_info(video_id: str):
    """Uses YouTube Data API to get video information"""
    try:
        api_url = f"https://www.googleapis.com/youtube/v3/videos"
        params = {
            'part': 'snippet,contentDetails',
            'id': video_id,
            'key': YOUTUBE_API_KEY
        }
        
        response = await asyncio.to_thread(requests.get, api_url, params=params, timeout=10)
        data = response.json()
        
        if 'items' in data and len(data['items']) > 0:
            item = data['items'][0]
            return {
                'title': item['snippet']['title'],
                'duration': item['contentDetails']['duration']
            }
        return None
    except Exception as e:
        logger.error(f"Error fetching video info: {e}")
        return None

async def download_media(url: str, video_id: str, format_type: str, temp_dir: str):
    """Downloads video/audio using pytubefix"""
    try:
        logger.info(f"Starting download for {url}")
        
        # Use pytubefix to download
        # --- THIS IS THE EDITED LINE ---
        yt = await asyncio.to_thread(YouTube, url, client='WEB', on_progress_callback=on_progress)
        
        if format_type == 'audio':
            # Get audio stream
            stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
            if not stream:
                logger.error("No audio stream found")
                return None
            
            # Download
            output_file = await asyncio.to_thread(stream.download, output_path=temp_dir, filename=f"{video_id}.mp3")
            logger.info(f"Audio downloaded: {output_file}")
            return output_file
        else:
            # Get video stream (720p or best available)
            stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
            if not stream:
                # Fallback to any mp4
                stream = yt.streams.filter(file_extension='mp4').first()
            
            if not stream:
                logger.error("No video stream found")
                return None
            
            # Download
            output_file = await asyncio.to_thread(stream.download, output_path=temp_dir, filename=f"{video_id}.mp4")
            logger.info(f"Video downloaded: {output_file}")
            return output_file
            
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}", exc_info=True)
        return None

# --- Bot Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text(
        "Hi! Send me a YouTube link and I'll help you download it as audio or video."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages to find YouTube links."""
    try:
        logger.info(f"Handling message from user {update.effective_user.id}")
        
        message_text = update.message.text
        logger.info(f"Message text: {message_text}")
        
        match = re.search(YOUTUBE_URL_REGEX, message_text)
        
        if match:
            video_id = match.group(1)
            url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Store URL in user_data
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
            
    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        raise

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
        # Get video info from YouTube API
        info = await get_video_info(video_id)
        if not info:
            await query.edit_message_text("Error: Could not get video information.")
            return

        title = info.get('title', 'Downloaded Media')
        
        with tempfile.TemporaryDirectory() as temp_dir:
            final_path = await download_media(url, video_id, format_type, temp_dir)
            
            if not final_path:
                await query.edit_message_text("Error: Failed to download the file.")
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
            
            # Send the file
            with open(final_path, 'rb') as f:
                if format_type == 'audio':
                    await update.effective_message.reply_audio(
                        audio=f,
                        title=title
                    )
                else:
                    await update.effective_message.reply_video(
                        video=f,
                        caption=title
                    )
            
            await query.delete_message()

    except Exception as e:
        logger.error(f"Main processing error for {url}: {e}", exc_info=True)
        try:
            await query.edit_message_text(f"An unexpected error occurred. Please try again.")
        except:
            pass

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Sorry, an error occurred while processing your request. Please try again."
            )
        except Exception as e:
            logger.error(f"Could not send error message to user: {e}")

# --- Initialize Telegram Bot Application ---
logger.info("Building Telegram bot application...")
ptb_app = Application.builder().token(TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
ptb_app.add_handler(CallbackQueryHandler(button_click))
ptb_app.add_error_handler(error_handler)

# --- Flask App ---
app = Flask(__name__)

@app.before_request
def init_bot():
    """Initialize bot before first request"""
    if not hasattr(app, 'bot_initialized'):
        logger.info("Initializing bot on first request...")
        try:
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
        
        update_json = request.get_json(force=True)
        if not update_json:
            logger.error("No JSON data in webhook request")
            return "no data", 400
        
        logger.info(f"Received update: {update_json.get('update_id', 'unknown')}")
        
        update = Update.de_json(update_json, ptb_app.bot)
        
        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        loop.run_until_complete(ptb_app.process_update(update))
        logger.info(f"Update {update.update_id} processed successfully")
        
        return "ok", 200
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return "error logged", 200

@app.route("/set_webhook")
def set_webhook():
    """Set the webhook URL for Telegram to send updates to"""
    try:
        render_url = os.environ.get("RENDER_EXTERNAL_URL") or "https://delight-yt-bot.onrender.com"
        
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
