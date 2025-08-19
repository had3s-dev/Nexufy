import os
import uuid
import shutil
import glob
from datetime import datetime, timedelta
from flask import Flask, render_template, request, send_from_directory, flash, redirect, url_for
from spotdl import Spotdl, AudioProviderError
from spotdl.download.downloader import Downloader
from spotdl.types.song import Song
from apscheduler.schedulers.background import BackgroundScheduler
from pydub import AudioSegment
import logging
import re
import tempfile
import requests

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'a_secure_random_secret_key')

# --- Railway-specific ffmpeg configuration ---
def find_ffmpeg():
    possible_paths = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "ffmpeg"  # System PATH
    ]

    for path in possible_paths:
        if shutil.which(path):
            logging.info(f"Found ffmpeg at: {path}")
            return path

    logging.warning("ffmpeg not found, pydub may not work properly")
    return "ffmpeg"  # Fallback to system PATH

def find_ffprobe():
    possible_paths = [
        "/usr/bin/ffprobe",
        "/usr/local/bin/ffprobe",
        "/opt/homebrew/bin/ffprobe",
        "ffprobe"  # System PATH
    ]

    for path in possible_paths:
        if shutil.which(path):
            logging.info(f"Found ffprobe at: {path}")
            return path

    logging.warning("ffprobe not found, pydub may not work properly")
    return "ffprobe"  # Fallback to system PATH

# Set ffmpeg paths dynamically for Railway
AudioSegment.converter = find_ffmpeg()
AudioSegment.ffprobe = find_ffprobe()

# --- Railway-optimized folder setup with temp directory ---
TEMP_BASE = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/tmp')
DOWNLOAD_FOLDER = os.path.join(TEMP_BASE, 'downloads')
CONVERTER_UPLOADS = os.path.join(TEMP_BASE, 'converter_uploads')
CONVERTER_OUTPUT = os.path.join(TEMP_BASE, 'converter_output')
COOKIES_FOLDER = os.path.join(TEMP_BASE, 'cookies')  # New folder for user cookies

for folder in [DOWNLOAD_FOLDER, CONVERTER_UPLOADS, CONVERTER_OUTPUT, COOKIES_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)
        logging.info(f"Created directory: {folder}")

# --- Helper Functions ---
def sanitize_name(name):
    if not name:
        return "guest"
    return re.sub(r'[^a-zA-Z0-9_-]', '', name).strip()[:50] or "guest"

def validate_cookies_file(content):
    """Validate that uploaded content is a proper cookies file"""
    lines = content.strip().split('\n')

    # Check for Netscape header
    if not lines[0].startswith('# Netscape HTTP Cookie File'):
        return False, "Invalid format: Missing Netscape header"

    # Check for YouTube/Google cookies
    has_youtube_cookies = False
    valid_lines = 0

    for line in lines[1:]:  # Skip header
        if line.startswith('#') or not line.strip():
            continue

        parts = line.split('\t')
        if len(parts) >= 7:
            domain = parts[0]
            if 'youtube.com' in domain or 'google.com' in domain:
                has_youtube_cookies = True
            valid_lines += 1
        else:
            return False, f"Invalid line format: {line[:50]}..."

    if not has_youtube_cookies:
        return False, "No YouTube/Google cookies found"

    if valid_lines < 3:
        return False, "Too few valid cookies"

    return True, f"Valid cookies file with {valid_lines} cookies"

def test_cookies_validity(cookies_path):
    """Test if cookies work by making a simple request"""
    try:
        import http.cookiejar
        import urllib.request

        # Load cookies
        jar = http.cookiejar.MozillaCookieJar(cookies_path)
        jar.load(ignore_discard=True, ignore_expires=True)

        # Test request to YouTube
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')]

        response = opener.open('https://www.youtube.com', timeout=10)

        if response.getcode() == 200:
            content = response.read().decode('utf-8')
            # Check for logged-in indicators
            if 'avatar' in content.lower() or 'channel' in content.lower():
                return True, "Cookies appear to be from logged-in session"
            else:
                return True, "Cookies work but may not be logged in"

        return False, f"HTTP {response.getcode()}"

    except Exception as e:
        return False, str(e)

def get_best_cookies():
    """Find the best working cookies from available options"""
    cookies_options = []

    # Option 1: Environment variable cookies
    env_cookies = os.environ.get('YOUTUBE_COOKIES_CONTENT')
    if env_cookies:
        env_path = os.path.join(TEMP_BASE, 'env_cookies.txt')
        try:
            with open(env_path, 'w') as f:
                f.write(env_cookies)

            is_working, status = test_cookies_validity(env_path)
            cookies_options.append({
                'path': env_path,
                'source': 'Environment Variable',
                'working': is_working,
                'status': status,
                'age': 0  # Always consider env cookies as "fresh"
            })
        except Exception as e:
            logging.error(f"Failed to create env cookies file: {e}")

    # Option 2: User uploaded cookies
    cookie_files = glob.glob(os.path.join(COOKIES_FOLDER, '*.txt'))
    for cookie_file in cookie_files:
        try:
            age_hours = (datetime.now() - datetime.fromtimestamp(os.path.getctime(cookie_file))).total_seconds() / 3600
            is_working, status = test_cookies_validity(cookie_file)

            cookies_options.append({
                'path': cookie_file,
                'source': f'User Upload: {os.path.basename(cookie_file)}',
                'working': is_working,
                'status': status,
                'age': age_hours
            })
        except Exception as e:
            logging.error(f"Error testing cookies {cookie_file}: {e}")

    # Sort by working status first, then by age (newer first)
    cookies_options.sort(key=lambda x: (not x['working'], x['age']))

    # Log all options
    logging.info(f"Found {len(cookies_options)} cookie options:")
    for i, option in enumerate(cookies_options):
        status_icon = "✅" if option['working'] else "❌"
        logging.info(f"  {i+1}. {status_icon} {option['source']} (Age: {option['age']:.1f}h) - {option['status']}")

    # Return the best working option
    for option in cookies_options:
        if option['working']:
            logging.info(f"Using cookies from: {option['source']}")
            return option['path']

    # If no working cookies, return the newest one and log warning
    if cookies_options:
        newest = cookies_options[0]
        logging.warning(f"No working cookies found, using newest: {newest['source']}")
        return newest['path']

    logging.warning("No cookies available")
    return None

def setup_youtube_cookies():
    """Setup YouTube cookies using the best available option"""
    best_cookies = get_best_cookies()

    if best_cookies:
        os.environ['YOUTUBE_COOKIES_FILE'] = best_cookies
        return True
    else:
        return False

# --- Railway-optimized cleanup scheduler ---
def cleanup_old_files():
    logging.info("Running scheduled cleanup of old files and folders...")
    now = datetime.now()
    cutoff = now - timedelta(hours=2)  # More aggressive cleanup for Railway's limited storage

    for base_folder in [DOWNLOAD_FOLDER, CONVERTER_UPLOADS, CONVERTER_OUTPUT]:
        try:
            if not os.path.exists(base_folder):
                continue

            for item_name in os.listdir(base_folder):
                item_path = os.path.join(base_folder, item_name)
                try:
                    if datetime.fromtimestamp(os.path.getctime(item_path)) < cutoff:
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                            logging.info(f"Deleted old folder: {item_path}")
                        else:
                            os.remove(item_path)
                            logging.info(f"Deleted old file: {item_path}")
                except (OSError, FileNotFoundError) as e:
                    logging.warning(f"Could not delete {item_path}: {e}")
        except Exception as e:
            logging.error(f"Error during cleanup of {base_folder}: {e}")

    # Clean old cookies (keep for 7 days)
    cookie_cutoff = now - timedelta(days=7)
    try:
        for cookie_file in glob.glob(os.path.join(COOKIES_FOLDER, '*.txt')):
            if datetime.fromtimestamp(os.path.getctime(cookie_file)) < cookie_cutoff:
                os.remove(cookie_file)
                logging.info(f"Deleted old cookies: {cookie_file}")
    except Exception as e:
        logging.error(f"Error cleaning cookies: {e}")

# More frequent cleanup for Railway
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(cleanup_old_files, 'interval', minutes=30)  # Every 30 minutes
scheduler.start()

# Setup cookies on startup
setup_youtube_cookies()

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/cookies')
def cookies_page():
    """Cookie management page"""
    cookie_files = []

    try:
        for cookie_file in glob.glob(os.path.join(COOKIES_FOLDER, '*.txt')):
            file_stat = os.stat(cookie_file)
            age = datetime.now() - datetime.fromtimestamp(file_stat.st_ctime)
            is_working, status = test_cookies_validity(cookie_file)

            cookie_files.append({
                'filename': os.path.basename(cookie_file),
                'age': f"{age.days} days, {age.seconds//3600} hours",
                'size': f"{file_stat.st_size} bytes",
                'working': is_working,
                'status': status,
                'uploaded': datetime.fromtimestamp(file_stat.st_ctime).strftime('%Y-%m-%d %H:%M')
            })
    except Exception as e:
        logging.error(f"Error reading cookie files: {e}")

    # Sort by working status, then by age
    cookie_files.sort(key=lambda x: (not x['working'], x['age']))

    return render_template('cookies.html', cookie_files=cookie_files)

@app.route('/cookies/upload', methods=['POST'])
def upload_cookies():
    """Handle cookies file upload"""
    if 'cookies_file' not in request.files:
        flash('No file selected', 'danger')
        return redirect(url_for('cookies_page'))

    file = request.files['cookies_file']
    user_name = sanitize_name(request.form.get('uploader_name', 'anonymous'))

    if file.filename == '':
        flash('No file selected', 'danger')
        return redirect(url_for('cookies_page'))

    try:
        # Read and validate file content
        content = file.read().decode('utf-8')
        is_valid, message = validate_cookies_file(content)

        if not is_valid:
            flash(f'Invalid cookies file: {message}', 'danger')
            return redirect(url_for('cookies_page'))

        # Save with timestamp and uploader name
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{user_name}_{timestamp}_cookies.txt"
        file_path = os.path.join(COOKIES_FOLDER, filename)

        with open(file_path, 'w') as f:
            f.write(content)

        # Test the cookies
        is_working, status = test_cookies_validity(file_path)
        status_msg = "✅ Working" if is_working else f"⚠️ {status}"

        flash(f'Cookies uploaded successfully! Status: {status_msg}', 'success' if is_working else 'warning')
        logging.info(f"New cookies uploaded by {user_name}: {filename} - {status}")

        # Refresh best cookies
        setup_youtube_cookies()

    except Exception as e:
        logging.error(f"Cookie upload failed: {e}")
        flash(f'Upload failed: {str(e)}', 'danger')

    return redirect(url_for('cookies_page'))

@app.route('/cookies/delete/<filename>')
def delete_cookies(filename):
    """Delete a cookies file"""
    try:
        file_path = os.path.join(COOKIES_FOLDER, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            flash(f'Deleted {filename}', 'success')
            logging.info(f"Deleted cookies file: {filename}")

            # Refresh best cookies
            setup_youtube_cookies()
        else:
            flash('File not found', 'danger')
    except Exception as e:
        logging.error(f"Failed to delete {filename}: {e}")
        flash(f'Delete failed: {str(e)}', 'danger')

    return redirect(url_for('cookies_page'))

@app.route('/process', methods=['POST'])
def process_download():
    """Process download with automatic cookie selection and proxy fallback."""
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

        spotify_client = Spotdl(client_id=client_id, client_secret=client_secret, headless=True)
        songs = spotify_client.search([url])

        if not songs:
            flash('WARNING: Could not find any songs for the given URL.', 'warning')
            shutil.rmtree(session_folder)
            return redirect(url_for('index'))

        is_playlist = len(songs) > 1
        download_path = None # Initialize download_path
        if is_playlist:
            playlist_name = songs[0].album or songs[0].artist or "Playlist"
            sanitized_playlist_name = "".join(c for c in playlist_name if c.isalnum() or c in (' ', '-')).rstrip()
            download_path = os.path.join(session_folder, sanitized_playlist_name)
            os.makedirs(download_path, exist_ok=True)
            output_format = os.path.join(download_path, "{title} - {artist}.{output-ext}")
        else:
            output_format = os.path.join(session_folder, "{title} - {artist}.{output-ext}")

        # --- DOWNLOAD LOGIC WITH PROXY FALLBACK ---
        proxy_url = os.environ.get('PROXY_URL')
        best_cookies = get_best_cookies()

        for song in songs:
            success = False

            # Attempt 1: With proxy (if available)
            if proxy_url:
                try:
                    logging.info(f"Attempting to download '{song.name}' with proxy...")
                    downloader_settings = {"simple_tui": True, "output": output_format}
                    yt_dlp_args = [
                        "--proxy", proxy_url, "--source-address", "0.0.0.0",
                        "--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3",
                        "--retry-sleep", "1", "--no-abort-on-error", "--ignore-errors",
                        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ]
                    if best_cookies:
                        yt_dlp_args.extend(["--cookies", best_cookies])
                    
                    downloader_settings["yt_dlp_args"] = " ".join(yt_dlp_args)
                    downloader = Downloader(settings=downloader_settings)
                    success, _ = downloader.download_song(song)
                except AudioProviderError as e:
                    logging.error(f"AudioProviderError with proxy: {e}")
                    success = False

            # Attempt 2: Without proxy (if first attempt failed or no proxy was set)
            if not success:
                try:
                    if proxy_url:
                        logging.warning(f"Download with proxy failed for '{song.name}'. Retrying without proxy...")
                    else:
                        logging.info(f"Attempting to download '{song.name}' (no proxy)...")

                    downloader_settings = {"simple_tui": True, "output": output_format}
                    yt_dlp_args = [
                        "--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3",
                        "--retry-sleep", "1", "--no-abort-on-error", "--ignore-errors",
                        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ]
                    if best_cookies:
                        yt_dlp_args.extend(["--cookies", best_cookies])

                    downloader_settings["yt_dlp_args"] = " ".join(yt_dlp_args)
                    downloader = Downloader(settings=downloader_settings)
                    success, _ = downloader.download_song(song)
                except AudioProviderError as e:
                    logging.error(f"AudioProviderError without proxy: {e}")
                    success = False
        
        # --- END OF DOWNLOAD LOGIC ---

        # Robust check: verify that files were actually created on disk.
        actual_output_dir = download_path if is_playlist else session_folder
        if os.path.exists(actual_output_dir) and os.listdir(actual_output_dir):
            if is_playlist:
                zip_filename_base = f"{sanitized_playlist_name}"
                zip_filepath = shutil.make_archive(os.path.join(session_folder, zip_filename_base), 'zip', download_path)
                final_filename = os.path.basename(zip_filepath)
                shutil.rmtree(download_path)
            else:
                # Now this is safe because we've confirmed the directory is not empty.
                final_filename = os.listdir(session_folder)[0]
            
            logging.info(f"Successfully prepared '{final_filename}' for user '{user_name}'")
            return render_template('index.html', download_link=True, user_name=user_name, session_id=session_id, filename=final_filename)
        else:
            flash('ERROR: Download failed. No audio file was created. The URL might be invalid or protected. Try uploading fresh cookies.', 'danger')
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)

    except Exception as e:
        logging.error(f"An error occurred for user '{user_name}': {e}", exc_info=True)
        flash(f'FATAL ERROR: {e}', 'danger')
        if os.path.exists(session_folder):
            shutil.rmtree(session_folder)

    return redirect(url_for('index'))

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
    # Railway provides PORT environment variable
    port = int(os.environ.get('PORT', 5000))
    # Railway runs on 0.0.0.0 by default
    app.run(host='0.0.0.0', port=port, debug=False)  # Disable debug in production
