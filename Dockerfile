FROM python:3.11-slim

# Install ffmpeg for audio/video processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot.py .

# Expose port for the web service
EXPOSE 8000

# Run the application with gunicorn
CMD ["gunicorn", "bot:app", "--bind", "0.0.0.0:8000", "--workers", "1", "--timeout", "120"]
