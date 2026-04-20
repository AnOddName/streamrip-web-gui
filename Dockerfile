FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -g 1000 appuser && \
    useradd -r -u 1000 -g appuser appuser

# Set working directory
WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    flask \
    flask-cors \
    streamrip \
    gunicorn \
    gevent

# Copy application files
COPY app.py /app/
COPY templates /app/templates/
COPY static /app/static/

# Create necessary directories with proper ownership
RUN mkdir -p /downloads /logs /config/streamrip && \
    chown -R 1000:1000 /downloads /logs /config

# Switch to non-root user
USER 1000:1000

# Expose port
EXPOSE 5000

# Run with aggressive worker recycling
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gevent", "--workers", "1", "--timeout", "60", "app:app"]
