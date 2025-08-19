import os
import uuid
import shutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request, send_from_directory, flash, redirect, url_for
from spotdl import Spotdl
from apscheduler.schedulers.background import BackgroundScheduler
import logging

# --- Configuration ---
# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Flask App
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'a_secure_random_secret_key')

# Configuration for file downloads
DOWNLOAD_FOLDER = 'downloads'
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# --- Spotdl Downloader Setup ---
def get_spotdl_instance():
    """Initializes and returns a Spotdl instance with proxy configuration."""
    proxy_url = os.environ.get('PROXY_URL')
    
    # Initialize Spotdl with no arguments, as the constructor is strict.
    spotdl = Spotdl()

    # Configure all settings on the instance's `args` property after initialization.
    config = {
        "output": "{title} - {artist}.{output-ext}",
        "format": "mp3",
        "log_level": "INFO",
    }

    if proxy_url:
        config["proxy"] = proxy_url
        logging.info(f"Using proxy: {proxy_url}")
    else:
        logging.warning("No PROXY_URL environment variable found. Running without a proxy.")
    
    spotdl.args.update(config)
        
    return spotdl

# --- Background Cleanup Scheduler ---
def cleanup_old_folders():
    """Removes download folders older than 12 hours."""
    logging.info("Running scheduled cleanup of old download folders...")
    now = datetime.now()
    cutoff = now - timedelta(hours=12)
    
    try:
        for folder_name in os.listdir(DOWNLOAD_FOLDER):
            folder_path = os.path.join(DOWNLOAD_FOLDER, folder_name)
            if os.path.isdir(folder_path):
                try:
                    folder_creation_time = datetime.fromtimestamp(os.path.getctime(folder_path))
                    if folder_creation_time < cutoff:
                        shutil.rmtree(folder_path)
                        logging.info(f"Deleted old folder: {folder_path}")
                except Exception as e:
                    logging.error(f"Error processing folder {folder_path}: {e}")
    except Exception as e:
        logging.error(f"An error occurred during cleanup: {e}")

# Initialize and start the scheduler
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(cleanup_old_folders, 'interval', hours=1)
scheduler.start()

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    """
    Handles the main page logic. 
    On POST, it processes the URL, downloads the audio, and provides a link.
    """
    if request.method == 'POST':
        url = request.form.get('url')
        if not url:
            flash('Please provide a Spotify or YouTube URL.', 'danger')
            return redirect(url_for('index'))

        # Create a unique session folder for the download
        session_id = str(uuid.uuid4())
        session_folder = os.path.join(DOWNLOAD_FOLDER, session_id)
        os.makedirs(session_folder, exist_ok=True)

        try:
            logging.info(f"Processing URL: {url} in session {session_id}")
            spotdl = get_spotdl_instance()
            
            # Change output directory for this specific download
            # This correctly combines the session folder with the output format string
            spotdl.args['output'] = os.path.join(session_folder, spotdl.args['output'])
            
            songs = spotdl.search([url])
            
            if not songs:
                flash('Could not find any songs for the given URL. Please check the link.', 'warning')
                shutil.rmtree(session_folder)
                return redirect(url_for('index'))

            # Download the songs
            results = spotdl.download_songs(songs)
            
            # Find the first successfully downloaded song
            downloaded_file = None
            for song, path in results:
                if path:
                    downloaded_file = os.path.basename(path)
                    break # We only handle the first file for simplicity

            if downloaded_file:
                logging.info(f"Successfully downloaded: {downloaded_file}")
                return render_template('index.html', 
                                       download_link=True, 
                                       session_id=session_id, 
                                       filename=downloaded_file)
            else:
                flash('Download failed. The URL might be invalid or protected.', 'danger')
                shutil.rmtree(session_folder) # Clean up empty folder

        except Exception as e:
            logging.error(f"An error occurred during download for session {session_id}: {e}", exc_info=True)
            flash(f'An unexpected error occurred: {e}', 'danger')
            shutil.rmtree(session_folder) # Clean up on error

        return redirect(url_for('index'))

    return render_template('index.html')


@app.route('/download/<session_id>/<filename>')
def download_file(session_id, filename):
    """Serves the downloaded file to the user."""
    directory = os.path.join(DOWNLOAD_FOLDER, session_id)
    logging.info(f"Serving file: {filename} from session: {session_id}")
    return send_from_directory(directory, filename, as_attachment=True)


@app.errorhandler(404)
def page_not_found(e):
    """Custom 404 error handler."""
    return render_template('index.html', error="404: Page not found."), 404


if __name__ == '__main__':
    # Note: This is for local development. Use Gunicorn for production.
    app.run(host='0.0.0.0', port=5000, debug=True)
