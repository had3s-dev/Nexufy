import os
import uuid
import shutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request, send_from_directory, flash, redirect, url_for
from spotdl import Spotdl
from spotdl.download.downloader import Downloader
from apscheduler.schedulers.background import BackgroundScheduler
import logging
import re

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'a_secure_random_secret_key')
DOWNLOAD_FOLDER = 'downloads'
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# --- Helper Functions ---
def sanitize_name(name):
    """Sanitizes a string to be used as a directory name."""
    if not name:
        return "guest"
    # Remove invalid characters and limit length
    return re.sub(r'[^a-zA-Z0-9_-]', '', name).strip()[:50] or "guest"

# --- Background Cleanup Scheduler ---
def cleanup_old_folders():
    """Removes download folders older than 12 hours."""
    logging.info("Running scheduled cleanup of old download folders...")
    now = datetime.now()
    cutoff = now - timedelta(hours=12)
    try:
        for user_folder_name in os.listdir(DOWNLOAD_FOLDER):
            user_folder_path = os.path.join(DOWNLOAD_FOLDER, user_folder_name)
            if os.path.isdir(user_folder_path):
                for session_folder_name in os.listdir(user_folder_path):
                    session_folder_path = os.path.join(user_folder_path, session_folder_name)
                    if os.path.isdir(session_folder_path):
                        try:
                            folder_creation_time = datetime.fromtimestamp(os.path.getctime(session_folder_path))
                            if folder_creation_time < cutoff:
                                shutil.rmtree(session_folder_path)
                                logging.info(f"Deleted old session folder: {session_folder_path}")
                        except Exception as e:
                            logging.error(f"Error processing folder {session_folder_path}: {e}")
    except Exception as e:
        logging.error(f"An error occurred during cleanup: {e}")

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(cleanup_old_folders, 'interval', hours=1)
scheduler.start()

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        url = request.form.get('url')
        user_name = sanitize_name(request.form.get('name'))

        if not url:
            flash('Please provide a Spotify or YouTube URL.', 'danger')
            return redirect(url_for('index'))

        session_id = str(uuid.uuid4())
        user_download_folder = os.path.join(DOWNLOAD_FOLDER, user_name)
        session_folder = os.path.join(user_download_folder, session_id)
        os.makedirs(session_folder, exist_ok=True)

        try:
            logging.info(f"Processing URL for user '{user_name}': {url} in session {session_id}")

            client_id = os.environ.get('SPOTIFY_CLIENT_ID')
            client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')

            if not client_id or not client_secret:
                raise ValueError("Spotify API credentials are not configured.")

            spotify_client = Spotdl(client_id=client_id, client_secret=client_secret)
            songs = spotify_client.search([url])

            if not songs:
                flash('Could not find any songs. Please check the link.', 'warning')
                shutil.rmtree(session_folder)
                return redirect(url_for('index'))

            is_playlist = len(songs) > 1
            if is_playlist:
                playlist_name = songs[0].album or songs[0].artist or "Playlist"
                sanitized_playlist_name = "".join(c for c in playlist_name if c.isalnum() or c in (' ', '-')).rstrip()
                download_path = os.path.join(session_folder, sanitized_playlist_name)
                os.makedirs(download_path, exist_ok=True)
                output_format = os.path.join(download_path, "{title} - {artist}.{output-ext}")
            else:
                output_format = os.path.join(session_folder, "{title} - {artist}.{output-ext}")

            downloader_settings = {"simple_tui": True, "output": output_format}

            # --- START OF CORRECTED PROXY IMPLEMENTATION ---
            proxy_url = os.environ.get('PROXY_URL')
            if proxy_url:
                logging.info(f"Attempting to use proxy: {proxy_url}")
                downloader_settings["yt_dlp_args"] = f"--proxy {proxy_url} --source-address 0.0.0.0"
            else:
                logging.warning("PROXY_URL environment variable not set. Proceeding without a proxy.")
            # --- END OF CORRECTED PROXY IMPLEMENTATION ---

            downloader = Downloader(settings=downloader_settings)
            
            downloaded_files_count = 0
            for song in songs:
                _, path = downloader.download_song(song)
                if path:
                    downloaded_files_count += 1

            if downloaded_files_count > 0:
                if is_playlist:
                    zip_filename_base = f"{sanitized_playlist_name}"
                    zip_filepath = shutil.make_archive(os.path.join(session_folder, zip_filename_base), 'zip', download_path)
                    final_filename = os.path.basename(zip_filepath)
                    shutil.rmtree(download_path)
                else:
                    final_filename = os.listdir(session_folder)[0]
                
                logging.info(f"Successfully prepared '{final_filename}' for user '{user_name}'")
                return render_template('index.html', download_link=True, user_name=user_name, session_id=session_id, filename=final_filename)
            else:
                flash('Download failed. The URL might be invalid or protected.', 'danger')
                shutil.rmtree(session_folder)

        except Exception as e:
            logging.error(f"An error occurred for user '{user_name}': {e}", exc_info=True)
            flash(f'An unexpected error occurred: {e}', 'danger')
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)

        return redirect(url_for('index'))

    return render_template('index.html')

@app.route('/downloads')
def downloads_page():
    """Displays a list of all available downloads, sorted by user and time."""
    all_downloads = []
    try:
        for user_name in sorted(os.listdir(DOWNLOAD_FOLDER)):
            user_folder_path = os.path.join(DOWNLOAD_FOLDER, user_name)
            if os.path.isdir(user_folder_path):
                user_files = []
                for session_id in os.listdir(user_folder_path):
                    session_folder_path = os.path.join(user_folder_path, session_id)
                    if os.path.isdir(session_folder_path):
                        for filename in os.listdir(session_folder_path):
                            file_path = os.path.join(session_folder_path, filename)
                            creation_time = datetime.fromtimestamp(os.path.getctime(session_folder_path))
                            user_files.append({
                                'user_name': user_name,
                                'session_id': session_id,
                                'filename': filename,
                                'timestamp': creation_time
                            })
                user_files.sort(key=lambda x: x['timestamp'], reverse=True)
                if user_files:
                    all_downloads.append({'user': user_name, 'files': user_files})
    except Exception as e:
        logging.error(f"Error reading download directory: {e}")
        flash("Could not load download history.", "danger")

    return render_template('downloads.html', downloads_by_user=all_downloads)

@app.route('/download/<user_name>/<session_id>/<filename>')
def download_file(user_name, session_id, filename):
    """Serves the downloaded file to the user."""
    directory = os.path.join(DOWNLOAD_FOLDER, user_name, session_id)
    logging.info(f"Serving file: {filename} for user: {user_name}")
    return send_from_directory(directory, filename, as_attachment=True)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
