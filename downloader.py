# downloader.py
import os
import sys
import json
from spotdl import Spotdl
from spotdl.types.options import DownloaderOptions

def download_url(url, proxy):
    """
    Initializes Spotdl and downloads songs from a given URL.
    Prints results as a JSON string.
    """
    downloader_options: DownloaderOptions = {
        'output': os.getenv('OUTPUT_PATH', default='/downloads/{artists} - {title}.{output-ext}'),
        'ffmpeg': '/usr/bin/ffmpeg',
        'audio_providers': ['youtube-music', 'youtube'],
        'lyrics_providers': ['genius', 'azlyrics'],
        'generate_lrc': False,
        'overwrite': 'skip',
        'restrict_filenames': False,
        'print_errors': True,
        'proxy': proxy
    }

    spotdl = Spotdl(
        client_id=os.getenv('CLIENT_ID', default='5f573c9620494bae87890c0f08a60293'),
        client_secret=os.getenv('CLIENT_SECRET', default='212476d9b0f3472eaa762d90b19b0ba8'),
        downloader_settings=downloader_options,
    )

    try:
        songs = spotdl.search([url])
        if not songs:
            print(json.dumps({"status": "error", "message": "No songs found for the given URL."}))
            return

        results = []
        for song in songs:
            try:
                _, download_path = spotdl.downloader.search_and_download(song)
                if download_path:
                    results.append(f"✅ Downloaded: {song.display_name}")
                else:
                    results.append(f"❌ Failed to download {song.display_name}: No path returned.")
            except Exception as e:
                results.append(f"❌ Error downloading {song.display_name}: {str(e)}")

        print(json.dumps({"status": "success", "results": results}))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        song_url = sys.argv[1]
        proxy_url = os.getenv('PROXY_URL', None)
        download_url(song_url, proxy_url)
    else:
        print(json.dumps({"status": "error", "message": "No URL provided to downloader script."}))