FROM python:3.13-slim

LABEL maintainer="Henrique Sebastião <contato@henriquesebastiao.com>"
LABEL version="0.3.2"
LABEL description="Self-hosted Spotify downloader"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHON_COLORS=0
ENV DOWNTIFY_PORT=8080

WORKDIR /downtify

COPY main.py requirements-app.txt entrypoint.sh ./
COPY templates ./templates
COPY assets ./assets
COPY static ./static

# Install system dependencies including gosu
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    wget \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Step 1: Fix entrypoint script line endings and permissions
RUN sed -i 's/\r$//g' entrypoint.sh && chmod +x entrypoint.sh

# Step 2: Install Python dependencies
RUN pip install --no-cache-dir --root-user-action ignore -r requirements-app.txt

# Step 3: Create symbolic link to system ffmpeg (already installed via apt)
RUN ln -s /usr/bin/ffmpeg /downtify/ffmpeg

ENV UID=1000
ENV GID=1000
ENV UMASK=022
ENV DOWNLOAD_DIR=/downloads

EXPOSE ${DOWNTIFY_PORT}

ENTRYPOINT ["./entrypoint.sh"]