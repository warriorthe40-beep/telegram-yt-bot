"""
Telegram YouTube Downloader Bot
Downloads YouTube videos/audio and sends them to users via Telegram.
Designed for Render free tier hosting with keep-alive mechanism.
"""

import os
import re
import asyncio
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
import yt_dlp
from keep_alive import keep_alive

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = Path("/tmp/downloads")
MAX_FILE_SIZE_MB = 50  # Telegram bot API limit
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Ensure download directory exists
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# YouTube URL patterns
YOUTUBE_PATTERNS = [
    r'(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]+',
    r'(https?://)?(www\.)?youtube\.com/shorts/[\w-]+',
    r'(https?://)?(www\.)?youtu\.be/[\w-]+',
    r'(https?://)?(music\.)?youtube\.com/watch\?v=[\w-]+',
    r'(https?://)?(www\.)?youtube\.com/playlist\?list=[\w-]+',
]

def is_youtube_url(text: str) -> bool:
    """Check if text contains a YouTube URL."""
    for pattern in YOUTUBE_PATTERNS:
        if re.search(pattern, text):
            return True
    return False

def extract_youtube_url(text: str) -> str | None:
    """Extract YouTube URL from text."""
    for pattern in YOUTUBE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None

def clean_filename(filename: str) -> str:
    """Clean filename for safe filesystem use."""
    # Remove or replace problematic characters
    cleaned = re.sub(r'[<>:"/\\|?*]', '', filename)
    cleaned = cleaned.strip()
    # Limit length
    if len(cleaned) > 100:
        cleaned = cleaned[:100]
    return cleaned

def get_file_size(filepath: Path) -> int:
    """Get file size in bytes."""
    return filepath.stat().st_size if filepath.exists() else 0

def cleanup_files(*filepaths: Path) -> None:
    """Delete files to free up space."""
    for filepath in filepaths:
        try:
            if filepath.exists():
                filepath.unlink()
                logger.info(f"Deleted: {filepath}")
        except Exception as e:
            logger.error(f"Failed to delete {filepath}: {e}")

def cleanup_download_dir() -> None:
    """Clean up all files in download directory."""
    try:
        for file in DOWNLOAD_DIR.iterdir():
            if file.is_file():
                file.unlink()
    except Exception as e:
        logger.error(f"Failed to cleanup download dir: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    welcome_message = """
🎬 *YouTube Downloader Bot*

Send me a YouTube link and I'll download it for you!

*Supported links:*
• Regular videos: `youtube.com/watch?v=...`
• Shorts: `youtube.com/shorts/...`
• Music: `music.youtube.com/watch?v=...`
• Short links: `youtu.be/...`

*Commands:*
/video `<url>` - Download as video (MP4)
/audio `<url>` - Download as audio (MP3)

Or just send a link and I'll ask what format you want!

⚠️ *Note:* Files larger than 50MB cannot be sent due to Telegram limits.
    """
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await start_command(update, context)

async def download_video(url: str, output_path: Path, max_height: int = 720) -> dict | None:
    """
    Download YouTube video with quality constraints.
    Returns video info dict or None if failed.
    """
    ydl_opts = {
        'format': f'bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best[height<={max_height}]',
        'outtmpl': str(output_path / '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        # Limit file size by duration and quality
        'max_filesize': MAX_FILE_SIZE_BYTES,
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info
    except Exception as e:
        logger.error(f"Video download failed: {e}")
        return None

async def download_audio(url: str, output_path: Path) -> dict | None:
    """
    Download YouTube audio as MP3.
    Returns audio info dict or None if failed.
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path / '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': 30,
        'retries': 3,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info
    except Exception as e:
        logger.error(f"Audio download failed: {e}")
        return None

def get_video_info(url: str) -> dict | None:
    """Get video info without downloading."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        return None

def find_downloaded_file(directory: Path, extensions: list[str]) -> Path | None:
    """Find the most recently downloaded file with given extensions."""
    files = []
    for ext in extensions:
        files.extend(directory.glob(f'*.{ext}'))
    
    if not files:
        return None
    
    # Return most recently modified file
    return max(files, key=lambda f: f.stat().st_mtime)

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /video command."""
    if not context.args:
        await update.message.reply_text("❌ Please provide a YouTube URL.\nUsage: `/video <url>`", parse_mode='Markdown')
        return
    
    url = context.args[0]
    if not is_youtube_url(url):
        await update.message.reply_text("❌ Invalid YouTube URL. Please send a valid YouTube link.")
        return
    
    await process_video_download(update, url)

async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /audio command."""
    if not context.args:
        await update.message.reply_text("❌ Please provide a YouTube URL.\nUsage: `/audio <url>`", parse_mode='Markdown')
        return
    
    url = context.args[0]
    if not is_youtube_url(url):
        await update.message.reply_text("❌ Invalid YouTube URL. Please send a valid YouTube link.")
        return
    
    await process_audio_download(update, url)

async def process_video_download(update: Update, url: str) -> None:
    """Process video download and send to user."""
    status_message = await update.message.reply_text("🔍 Fetching video info...")
    
    try:
        # Show typing indicator
        await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
        
        # Clean download directory first
        cleanup_download_dir()
        
        # Update status
        await status_message.edit_text("⬇️ Downloading video (720p max)...")
        
        # Download with 720p limit first
        info = await asyncio.get_event_loop().run_in_executor(
            None, 
            lambda: asyncio.run(download_video(url, DOWNLOAD_DIR, 720))
        )
        
        # Actually run synchronously since yt-dlp isn't async
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: download_video_sync(url, DOWNLOAD_DIR, 720))
        
        if not info:
            await status_message.edit_text("❌ Download failed. The video might be unavailable or too large.")
            return
        
        # Find the downloaded file
        video_file = find_downloaded_file(DOWNLOAD_DIR, ['mp4', 'mkv', 'webm'])
        
        if not video_file:
            await status_message.edit_text("❌ Downloaded file not found.")
            return
        
        file_size = get_file_size(video_file)
        
        # Check file size
        if file_size > MAX_FILE_SIZE_BYTES:
            # Try lower quality
            cleanup_download_dir()
            await status_message.edit_text("📉 File too large, trying 480p...")
            
            info = await loop.run_in_executor(None, lambda: download_video_sync(url, DOWNLOAD_DIR, 480))
            video_file = find_downloaded_file(DOWNLOAD_DIR, ['mp4', 'mkv', 'webm'])
            
            if not video_file or get_file_size(video_file) > MAX_FILE_SIZE_BYTES:
                cleanup_download_dir()
                await status_message.edit_text(
                    f"❌ Video is too large (>{MAX_FILE_SIZE_MB}MB) even at 480p.\n"
                    "Try downloading as audio instead with `/audio`",
                    parse_mode='Markdown'
                )
                return
        
        # Send the video
        await status_message.edit_text("📤 Uploading to Telegram...")
        await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
        
        title = info.get('title', 'video')[:100]
        duration = info.get('duration', 0)
        
        with open(video_file, 'rb') as f:
            await update.message.reply_video(
                video=f,
                caption=f"🎬 {title}",
                supports_streaming=True,
                duration=duration,
                read_timeout=120,
                write_timeout=120,
            )
        
        await status_message.delete()
        logger.info(f"Successfully sent video: {title}")
        
    except Exception as e:
        logger.error(f"Error processing video: {e}")
        await status_message.edit_text(f"❌ Error: {str(e)[:200]}")
    
    finally:
        cleanup_download_dir()

async def process_audio_download(update: Update, url: str) -> None:
    """Process audio download and send to user."""
    status_message = await update.message.reply_text("🔍 Fetching audio info...")
    
    try:
        await update.message.chat.send_action(ChatAction.UPLOAD_AUDIO)
        
        cleanup_download_dir()
        
        await status_message.edit_text("⬇️ Downloading audio...")
        
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: download_audio_sync(url, DOWNLOAD_DIR))
        
        if not info:
            await status_message.edit_text("❌ Download failed. The video might be unavailable.")
            return
        
        # Find the downloaded file
        audio_file = find_downloaded_file(DOWNLOAD_DIR, ['mp3', 'm4a', 'opus', 'wav'])
        
        if not audio_file:
            await status_message.edit_text("❌ Downloaded file not found.")
            return
        
        file_size = get_file_size(audio_file)
        
        if file_size > MAX_FILE_SIZE_BYTES:
            await status_message.edit_text(f"❌ Audio is too large (>{MAX_FILE_SIZE_MB}MB).")
            cleanup_download_dir()
            return
        
        # Send the audio
        await status_message.edit_text("📤 Uploading to Telegram...")
        await update.message.chat.send_action(ChatAction.UPLOAD_AUDIO)
        
        title = info.get('title', 'audio')[:100]
        duration = info.get('duration', 0)
        
        with open(audio_file, 'rb') as f:
            await update.message.reply_audio(
                audio=f,
                caption=f"🎵 {title}",
                title=title,
                duration=duration,
                read_timeout=120,
                write_timeout=120,
            )
        
        await status_message.delete()
        logger.info(f"Successfully sent audio: {title}")
        
    except Exception as e:
        logger.error(f"Error processing audio: {e}")
        await status_message.edit_text(f"❌ Error: {str(e)[:200]}")
    
    finally:
        cleanup_download_dir()

def download_video_sync(url: str, output_path: Path, max_height: int = 720) -> dict | None:
    """Synchronous video download."""
    ydl_opts = {
        'format': f'bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best[height<={max_height}]',
        'outtmpl': str(output_path / '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info
    except Exception as e:
        logger.error(f"Video download failed: {e}")
        return None

def download_audio_sync(url: str, output_path: Path) -> dict | None:
    """Synchronous audio download."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path / '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info
    except Exception as e:
        logger.error(f"Audio download failed: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages with YouTube links."""
    text = update.message.text
    
    if not text:
        return
    
    url = extract_youtube_url(text)
    
    if not url:
        # Not a YouTube URL, ignore
        return
    
    # Ask user what format they want
    await update.message.reply_text(
        f"🎬 YouTube link detected!\n\n"
        f"What would you like to download?\n\n"
        f"• Send `/video {url}` for video (MP4)\n"
        f"• Send `/audio {url}` for audio only (MP3)",
        parse_mode='Markdown'
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.message:
        await update.message.reply_text(
            "❌ An error occurred. Please try again later."
        )

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set!")
        print("ERROR: Please set the BOT_TOKEN environment variable")
        return
    
    # Start keep-alive server
    keep_alive()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("video", video_command))
    application.add_handler(CommandHandler("audio", audio_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start polling
    logger.info("Bot starting...")
    print("✅ Bot is running!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
