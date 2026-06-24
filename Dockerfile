FROM python:3.11-slim

# Install ffmpeg and ffprobe (with all codecs for broad video support)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create temp dirs (also created at runtime, but good to have in image)
RUN mkdir -p /tmp/video2gif_uploads /tmp/video2gif_outputs

EXPOSE 8080

ENV PORT=8080

CMD gunicorn app:app \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --timeout 300 \
    --worker-class sync \
    --log-level info
