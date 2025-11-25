"""
Telegram YouTube Downloader Bot v2
Fixed version with better error handling and FFmpeg support.
"""

import os
import re
import asyncio
import logging
import traceback
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from telegram.error import TimedOut, NetworkError
import yt_dlp
from keep_alive import keep_alive

# Configure logging - more verbose for debugging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = Path("/tmp/downloads")
MAX_FILE_SIZE_MB = 50
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Ensure download directory exists
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# YouTube URL patterns
YOUTUBE_PATTERNS = [
    r'(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]+',
    r'(https?://)?(www\.)?youtube\.com/shorts/[\w-]+',
    r'(https?://)?(www\.)?youtu\.be/[\w-]+',
    r'(https?://)?(music\.)?youtube\.com/watch\?v=[\w-]+',
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

def get_file_size(filepath: Path) -> int:
    """Get file size in bytes."""
    return filepath.stat().st_size if filepath.exists() else 0

def cleanup_download_dir() -> None:
    """Clean up all files in download directory."""
    try:
        for file in DOWNLOAD_DIR.iterdir():
            if file.is_file():
                file.unlink()
                logger.info(f"Cleaned up: {file}")
    except Exception as e:
        logger.error(f"Failed to cleanup download dir: {e}")

def find_downloaded_file(directory: Path, extensions: list[str]) -> Path | None:
    """Find the most recently downloaded file with given extensions."""
    files = []
    for ext in extensions:
        files.extend(directory.glob(f'*.{ext}'))
    
    if not files:
        return None
    
    return max(files, key=lambda f: f.stat().st_mtime)

def download_video_sync(url: str, output_path: Path, max_height: int = 720) -> tuple[dict | None, str | None]:
    """
    Synchronous video download.
    Returns (info_dict, error_message)
    """
    ydl_opts = {
        # Simpler format selection that works without merging when possible
        'format': f'best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best',
        'outtmpl': str(output_path / '%(title).100s.%(ext)s'),
        'quiet': False,
        'no_warnings': False,
        'socket_timeout': 60,
        'retries': 3,
        'fragment_retries': 3,
        'ignoreerrors': False,
        'no_color': True,
        # Prefer formats that don't need merging
        'prefer_free_formats': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Starting video download: {url}")
            info = ydl.extract_info(url, download=True)
            logger.info(f"Download completed: {info.get('title', 'Unknown')}")
            return info, None
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp DownloadError: {error_msg}")
        return None, f"Download error: {error_msg[:200]}"
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Video download failed: {error_msg}")
        logger.error(traceback.format_exc())
        return None, f"Error: {error_msg[:200]}"

def download_audio_sync(url: str, output_path: Path) -> tuple[dict | None, str | None]:
    """
    Synchronous audio download.
    Returns (info_dict, error_message)
    """
    # First, try downloading audio with FFmpeg conversion
    ydl_opts_with_ffmpeg = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path / '%(title).100s.%(ext)s'),
        'quiet': False,
        'no_warnings': False,
        'socket_timeout': 60,
        'retries': 3,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    # Fallback options without FFmpeg post-processing
    ydl_opts_no_ffmpeg = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'outtmpl': str(output_path / '%(title).100s.%(ext)s'),
        'quiet': False,
        'no_warnings': False,
        'socket_timeout': 60,
        'retries': 3,
    }
    
    # Try with FFmpeg first
    try:
        with yt_dlp.YoutubeDL(ydl_opts_with_ffmpeg) as ydl:
            logger.info(f"Starting audio download (with FFmpeg): {url}")
            info = ydl.extract_info(url, download=True)
            logger.info(f"Audio download completed: {info.get('title', 'Unknown')}")
            return info, None
    except Exception as e:
        logger.warning(f"FFmpeg conversion failed, trying without: {e}")
    
    # Fallback without FFmpeg
    try:
        with yt_dlp.YoutubeDL(ydl_opts_no_ffmpeg) as ydl:
            logger.info(f"Starting audio download (without FFmpeg): {url}")
            info = ydl.extract_info(url, download=True)
            logger.info(f"Audio download completed: {info.get('title', 'Unknown')}")
            return info, None
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp DownloadError: {error_msg}")
        return None, f"Download error: {error_msg[:200]}"
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Audio download failed: {error_msg}")
        logger.error(traceback.format_exc())
        return None, f"Error: {error_msg[:200]}"

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
/audio `<url>` - Download as audio (M4A/MP3)

Or just send a link and I'll ask what format you want!

⚠️ *Note:* Files larger than 50MB cannot be sent.
    """
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await start_command(update, context)

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /video command."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a YouTube URL.\n"
            "Usage: `/video <url>`", 
            parse_mode='Markdown'
        )
        return
    
    url = context.args[0]
    if not is_youtube_url(url):
        await update.message.reply_text("❌ Invalid YouTube URL.")
        return
    
    await process_video_download(update, url)

async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /audio command."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a YouTube URL.\n"
            "Usage: `/audio <url>`", 
            parse_mode='Markdown'
        )
        return
    
    url = context.args[0]
    if not is_youtube_url(url):
        await update.message.reply_text("❌ Invalid YouTube URL.")
        return
    
    await process_audio_download(update, url)

async def process_video_download(update: Update, url: str) -> None:
    """Process video download and send to user."""
    status_message = await update.message.reply_text("🔍 Fetching video...")
    
    try:
        await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
        cleanup_download_dir()
        
        await status_message.edit_text("⬇️ Downloading video (720p)...")
        
        # Run download in thread pool
        loop = asyncio.get_event_loop()
        info, error = await loop.run_in_executor(
            None, 
            lambda: download_video_sync(url, DOWNLOAD_DIR, 720)
        )
        
        if error:
            await status_message.edit_text(f"❌ {error}")
            cleanup_download_dir()
            return
        
        if not info:
            await status_message.edit_text("❌ Download failed. No video info returned.")
            cleanup_download_dir()
            return
        
        # Find downloaded file
        video_file = find_downloaded_file(DOWNLOAD_DIR, ['mp4', 'mkv', 'webm', 'mov'])
        
        if not video_file:
            # List what files exist for debugging
            files = list(DOWNLOAD_DIR.iterdir())
            logger.error(f"No video file found. Files in dir: {files}")
            await status_message.edit_text("❌ Downloaded file not found.")
            cleanup_download_dir()
            return
        
        file_size = get_file_size(video_file)
        logger.info(f"Downloaded file: {video_file}, size: {file_size / 1024 / 1024:.2f} MB")
        
        # Check file size
        if file_size > MAX_FILE_SIZE_BYTES:
            # Try 480p
            cleanup_download_dir()
            await status_message.edit_text("📉 File too large, trying 480p...")
            
            info, error = await loop.run_in_executor(
                None,
                lambda: download_video_sync(url, DOWNLOAD_DIR, 480)
            )
            
            if error:
                await status_message.edit_text(f"❌ {error}")
                cleanup_download_dir()
                return
            
            video_file = find_downloaded_file(DOWNLOAD_DIR, ['mp4', 'mkv', 'webm', 'mov'])
            
            if not video_file or get_file_size(video_file) > MAX_FILE_SIZE_BYTES:
                size_mb = get_file_size(video_file) / 1024 / 1024 if video_file else 0
                await status_message.edit_text(
                    f"❌ Video too large ({size_mb:.1f}MB > 50MB).\n"
                    f"Try `/audio` for audio only.",
                    parse_mode='Markdown'
                )
                cleanup_download_dir()
                return
        
        # Send video
        await status_message.edit_text("📤 Uploading to Telegram...")
        await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
        
        title = info.get('title', 'video')[:100]
        duration = info.get('duration', 0)
        
        try:
            with open(video_file, 'rb') as f:
                await update.message.reply_video(
                    video=f,
                    caption=f"🎬 {title}",
                    supports_streaming=True,
                    duration=duration,
                    read_timeout=300,
                    write_timeout=300,
                    connect_timeout=60,
                )
            await status_message.delete()
            logger.info(f"Successfully sent video: {title}")
            
        except TimedOut:
            await status_message.edit_text(
                "❌ Upload timed out. File might be too large.\n"
                "Try `/audio` for smaller file.",
                parse_mode='Markdown'
            )
        except NetworkError as e:
            await status_message.edit_text(f"❌ Network error during upload: {str(e)[:100]}")
        
    except Exception as e:
        logger.error(f"Error processing video: {e}")
        logger.error(traceback.format_exc())
        try:
            await status_message.edit_text(f"❌ Error: {str(e)[:200]}")
        except:
            pass
    
    finally:
        cleanup_download_dir()

async def process_audio_download(update: Update, url: str) -> None:
    """Process audio download and send to user."""
    status_message = await update.message.reply_text("🔍 Fetching audio...")
    
    try:
        await update.message.chat.send_action(ChatAction.UPLOAD_AUDIO)
        cleanup_download_dir()
        
        await status_message.edit_text("⬇️ Downloading audio...")
        
        # Run download in thread pool
        loop = asyncio.get_event_loop()
        info, error = await loop.run_in_executor(
            None,
            lambda: download_audio_sync(url, DOWNLOAD_DIR)
        )
        
        if error:
            await status_message.edit_text(f"❌ {error}")
            cleanup_download_dir()
            return
        
        if not info:
            await status_message.edit_text("❌ Download failed.")
            cleanup_download_dir()
            return
        
        # Find downloaded file
        audio_file = find_downloaded_file(DOWNLOAD_DIR, ['mp3', 'm4a', 'opus', 'webm', 'wav', 'ogg'])
        
        if not audio_file:
            files = list(DOWNLOAD_DIR.iterdir())
            logger.error(f"No audio file found. Files in dir: {files}")
            await status_message.edit_text("❌ Downloaded file not found.")
            cleanup_download_dir()
            return
        
        file_size = get_file_size(audio_file)
        logger.info(f"Downloaded audio: {audio_file}, size: {file_size / 1024 / 1024:.2f} MB")
        
        if file_size > MAX_FILE_SIZE_BYTES:
            await status_message.edit_text(f"❌ Audio too large ({file_size / 1024 / 1024:.1f}MB > 50MB).")
            cleanup_download_dir()
            return
        
        # Send audio
        await status_message.edit_text("📤 Uploading to Telegram...")
        await update.message.chat.send_action(ChatAction.UPLOAD_AUDIO)
        
        title = info.get('title', 'audio')[:100]
        duration = info.get('duration', 0)
        
        try:
            with open(audio_file, 'rb') as f:
                await update.message.reply_audio(
                    audio=f,
                    caption=f"🎵 {title}",
                    title=title,
                    duration=duration,
                    read_timeout=300,
                    write_timeout=300,
                    connect_timeout=60,
                )
            await status_message.delete()
            logger.info(f"Successfully sent audio: {title}")
            
        except TimedOut:
            await status_message.edit_text("❌ Upload timed out.")
        except NetworkError as e:
            await status_message.edit_text(f"❌ Network error: {str(e)[:100]}")
        
    except Exception as e:
        logger.error(f"Error processing audio: {e}")
        logger.error(traceback.format_exc())
        try:
            await status_message.edit_text(f"❌ Error: {str(e)[:200]}")
        except:
            pass
    
    finally:
        cleanup_download_dir()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages with YouTube links."""
    text = update.message.text
    
    if not text:
        return
    
    url = extract_youtube_url(text)
    
    if not url:
        return
    
    await update.message.reply_text(
        f"🎬 YouTube link detected!\n\n"
        f"Choose format:\n"
        f"• `/video {url}` - Video (MP4)\n"
        f"• `/audio {url}` - Audio only",
        parse_mode='Markdown'
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error(f"Error: {context.error}")
    logger.error(traceback.format_exc())
    
    if update and update.message:
        try:
            await update.message.reply_text("❌ An error occurred. Please try again.")
        except:
            pass

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set!")
        return
    
    # Check FFmpeg
    import shutil
    if shutil.which('ffmpeg'):
        print("✅ FFmpeg found")
    else:
        print("⚠️ FFmpeg not found - audio conversion may not work")
    
    # Start keep-alive server
    keep_alive()
    
    # Create application with longer timeouts
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .build()
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("video", video_command))
    application.add_handler(CommandHandler("audio", audio_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    print("✅ Bot is running!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
