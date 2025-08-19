import os
import uuid
import shutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request, send_from_directory, flash, redirect, url_for
from spotdl import Spotdl
from spotdl.download.downloader import Downloader
from apscheduler.schedulers.background import BackgroundScheduler
from pydub import AudioSegment
import logging
import re

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'a_secure_random_secret_key')

# --- Tell pydub where to find ffmpeg and ffprobe ---
AudioSegment.converter = "/usr/bin/ffmpeg"
AudioSegment.ffprobe = "/usr/bin/ffprobe"

# --- Folder Setup ---
DOWNLOAD_FOLDER = 'downloads'
CONVERTER_UPLOADS = 'converter_uploads'
CONVERTER_OUTPUT = 'converter_output'

for folder in [DOWNLOAD_FOLDER, CONVERTER_UPLOADS, CONVERTER_OUTPUT]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# --- Helper Functions ---
def sanitize_name(name):
    if not name:
        return "guest"
    return re.sub(r'[^a_zA-Z0-9_-]', '', name).strip()[:50] or "guest"

# --- Background Cleanup Scheduler ---
def cleanup_old_files():
    logging.info("Running scheduled cleanup of old files and folders...")
    now = datetime.now()
    cutoff = now - timedelta(hours=12)
    
    for base_folder in [DOWNLOAD_FOLDER, CONVERTER_UPLOADS, CONVERTER_OUTPUT]:
        try:
            for item_name in os.listdir(base_folder):
                item_path = os.path.join(base_folder, item_name)
                if datetime.fromtimestamp(os.path.getctime(item_path)) < cutoff:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                        logging.info(f"Deleted old folder: {item_path}")
                    else:
                        os.remove(item_path)
                        logging.info(f"Deleted old file: {item_path}")
        except Exception as e:
            logging.error(f"Error during cleanup of {base_folder}: {e}")

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(cleanup_old_files, 'interval', hours=1)
scheduler.start()

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        url = request.form.get('url')
        user_name = sanitize_name(request.form.get('name'))

        if not url:
            flash('ERROR: No URL provided.', 'danger')
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

            # Initialize Spotdl for searching ONLY. It does not need proxy info.
            spotify_client = Spotdl(
                client_id=client_id,
                client_secret=client_secret,
                headless=True
            )

            songs = spotify_client.search([url])

            if not songs:
                flash('WARNING: Could not find any songs for the given URL.', 'warning')
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

            # --- CORRECT AND FINAL PROXY IMPLEMENTATION ---
            # The proxy settings are passed ONLY to the Downloader via yt_dlp_args.
            downloader_settings = {"simple_tui": True, "output": output_format}
            proxy_url = os.environ.get('PROXY_URL')
            if proxy_url:
                logging.info(f"Attempting to use proxy: {proxy_url}")
                downloader_settings["yt_dlp_args"] = f"--proxy {proxy_url} --source-address 0.0.0.0"
            else:
                logging.warning("PROXY_URL not set. Proceeding without proxy.")
            
            # Initialize the downloader with these settings.
            # DO NOT pass the spotdl_instance here.
            downloader = Downloader(settings=downloader_settings)
            # --- END OF PROXY FIX ---
            
            downloaded_files_count = sum(1 for song in songs if downloader.download_song(song)[1])

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
                flash('ERROR: Download failed. The URL might be invalid or protected.', 'danger')
                shutil.rmtree(session_folder)

        except Exception as e:
            logging.error(f"An error occurred for user '{user_name}': {e}", exc_info=True)
            flash(f'FATAL ERROR: {e}', 'danger')
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)

        return redirect(url_for('index'))

    return render_template('index.html')

@app.route('/converter', methods=['GET', 'POST'])
def converter_page():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('ERROR: No file part in request.', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('ERROR: No file selected.', 'danger')
            return redirect(request.url)

        target_format = request.form.get('format', 'mp3')
        allowed_formats = ['mp3', 'wav', 'flac', 'ogg']

        if target_format not in allowed_formats:
            flash('ERROR: Invalid target format.', 'danger')
            return redirect(request.url)

        if file:
            temp_id = str(uuid.uuid4())
            upload_path = os.path.join(CONVERTER_UPLOADS, temp_id)
            
            try:
                file.save(upload_path)
                logging.info(f"Converting {file.filename} to {target_format}")
                audio = AudioSegment.from_file(upload_path)
                
                output_filename = f"{os.path.splitext(file.filename)[0]}.{target_format}"
                output_path = os.path.join(CONVERTER_OUTPUT, output_filename)
                audio.export(output_path, format=target_format)

                return render_template('converter.html', conversion_complete=True, filename=output_filename)

            except Exception as e:
                logging.error(f"Conversion failed: {e}", exc_info=True)
                flash(f"ERROR: Conversion failed. The uploaded file may not be a valid audio format. Details: {e}", 'danger')
                return redirect(request.url)
            finally:
                if os.path.exists(upload_path):
                    os.remove(upload_path)

    return render_template('converter.html')

@app.route('/download_converted/<filename>')
def download_converted_file(filename):
    logging.info(f"Serving converted file: {filename}")
    return send_from_directory(CONVERTER_OUTPUT, filename, as_attachment=True)

@app.route('/downloads')
def downloads_page():
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
    directory = os.path.join(DOWNLOAD_FOLDER, user_name, session_id)
    logging.info(f"Serving file: {filename} for user: {user_name}")
    return send_from_directory(directory, filename, as_attachment=True)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
