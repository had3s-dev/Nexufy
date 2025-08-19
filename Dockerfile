# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# We use --no-cache-dir to reduce image size
# And we upgrade spotdl and yt-dlp to the latest versions to get the latest patches
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade spotdl yt-dlp

# Install ffmpeg which is required by spotdl
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy the content of the local src directory to the working directory
COPY . .

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Define environment variable for the Flask app
ENV FLASK_APP=main.py

# Run the application using Gunicorn with an increased timeout
# The --timeout 300 flag allows each worker up to 300 seconds to complete a request.
# This is the key change to prevent crashes on long downloads.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "--timeout", "300", "--preload", "main:app"]
