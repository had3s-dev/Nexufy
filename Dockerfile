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

# Install system dependencies including ffmpeg and certificates
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Make entrypoint executable and install Python dependencies
RUN sed -i 's/\r$//g' entrypoint.sh && \
    chmod +x entrypoint.sh && \
    pip install --no-cache-dir --root-user-action ignore -r requirements-app.txt && \
    spotdl --download-ffmpeg && \
    cp /root/.spotdl/ffmpeg /downtify

ENV UID=1000
ENV GID=1000
ENV UMASK=022
ENV DOWNLOAD_DIR=/downloads

EXPOSE ${DOWNTIFY_PORT}

# ENTRYPOINT removed — Railway.json will handle start command