import os
from functools import lru_cache
import asyncio
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from spotdl import Spotdl
from spotdl.types.options import DownloaderOptions
from starlette.requests import Request

load_dotenv()

DESCRIPTION = """
Download Spotify music with album art and metadata.

With Downtify you can download Spotify musics containing album art, track names, album title and other metadata about the songs.
"""


class Message(BaseModel):
    message: str = Field(examples=['Download sucessful'])


app = FastAPI(
    title='Downtify',
    version='0.3.2',
    description=DESCRIPTION,
    contact={
        'name': 'Downtify',
        'url': 'https://github.com/henriquesebastiao/downtify',
        'email': 'contato@henriquesebastiao.com',
    },
    terms_of_service='https://github.com/henriquesebastiao/downtify/',
)


app.mount('/static', StaticFiles(directory='static'), name='static')
app.mount('/assets', StaticFiles(directory='assets'), name='assets')

if not os.path.exists('/downloads'):
    os.makedirs('/downloads')

app.mount('/downloads', StaticFiles(directory='/downloads'), name='downloads')
templates = Jinja2Templates(directory='templates')

@lru_cache(maxsize=1)
def get_spotdl():
    """
    Initializes and returns a Spotdl instance.
    Using lru_cache to reuse the same instance across requests.
    """
    proxy_url = os.getenv('PROXY_URL', None)
    print(f"Initializing Spotdl with proxy: {proxy_url}")

    downloader_options: DownloaderOptions = {
        'output': os.getenv(
            'OUTPUT_PATH', default='/downloads/{artists} - {title}.{output-ext}'
        ),
        'ffmpeg': '/usr/bin/ffmpeg',
        'audio_providers': ['youtube-music', 'youtube'],
        'lyrics_providers': ['genius', 'azlyrics'],
        'generate_lrc': False,
        'overwrite': 'skip',
        'restrict_filenames': False,
        'print_errors': True,
        'proxy': proxy_url
    }

    return Spotdl(
        client_id=os.getenv(
            'CLIENT_ID', default='5f573c9620494bae87890c0f08a60293'
        ),
        client_secret=os.getenv(
            'CLIENT_SECRET', default='212476d9b0f3472eaa762d90b19b0ba8'
        ),
        downloader_settings=downloader_options,
    )

def get_downloaded_files() -> str:
    download_path = '/downloads'
    try:
        files = os.listdir(download_path)
        file_links = [
            f'<li class="list-group-item"><a href="/downloads/{file}">{file}</a></li>'
            for file in files
        ]
        files = (
            ''.join(file_links)
            if file_links
            else '<li class="list-group-item">No files found.</li>'
        )
    except Exception as e:
        files = f'<li class="list-group-item text-danger">Error: {str(e)}</li>'

    return files


@app.get(
    '/',
    response_class=HTMLResponse,
    tags=['Web UI'],
    summary='Application web interface',
)
def index(request: Request):
    return templates.TemplateResponse('index.html', {'request': request})


def download_songs_sync(spotdlc, songs):
    """Synchronous download function to run in thread pool"""
    results = []
    for song in songs:
        try:
            print(f"Attempting to download: {song.display_name}")
            # The result is a tuple (song, path), we don't need the path here
            song_object, download_path = spotdlc.downloader.search_and_download(song)
            if download_path:
                 results.append(f"✅ Downloaded: {song.display_name}")
            else:
                results.append(f"❌ Failed to download {song.display_name}: No download path returned.")
        except Exception as e:
            # Log the specific error and continue with next song
            error_msg = f"❌ Failed to download {song.display_name}: {e}"
            results.append(error_msg)
            print(f"Detailed download error for {song.display_name}:")
            import traceback
            traceback.print_exc()
    return results


@app.post(
    '/download-web',
    response_class=HTMLResponse,
    tags=['Downloader'],
    summary='Download one or more songs from a playlist via the WEB interface',
)
async def download_web_ui(
    spotdlc: Spotdl = Depends(get_spotdl),
    url: str = Form(...),
):
    """
    You can download a single song or all the songs in a playlist, album, etc.

    - **url**: URL of the song or playlist to download.

    ### Responses

    - `200` - Download successful.
    """
    try:
        print(f"Searching for: {url}")
        songs = spotdlc.search([url])
        print(f"Found {len(songs)} songs: {[song.display_name for song in songs]}")
        
        # Run download in a separate thread with timeout
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            try:
                # Add 60 second timeout for the entire download process
                results = await asyncio.wait_for(
                    loop.run_in_executor(executor, download_songs_sync, spotdlc, songs),
                    timeout=60.0
                )
            except asyncio.TimeoutError:
                return f"""
            <div>
                <button type="submit" class="btn btn-lg btn-light fw-bold border-white button mx-auto" id="button-download" style="display: block;"><i class="fa-solid fa-down-long"></i></button>
                <div class="alert alert-warning mx-auto" id="success-card" style="display: none;">
                    <strong>Download timed out after 60 seconds</strong>
                </div>
            </div>
            """
        
        print(f"Download results: {results}")
    except Exception as error:
        return f"""
    <div>
        <button type="submit" class="btn btn-lg btn-light fw-bold border-white button mx-auto" id="button-download" style="display: block;"><i class="fa-solid fa-down-long"></i></button>
        <div class="alert alert-danger mx-auto" id="success-card" style="display: none;">
            <strong>Error: {error}</strong>
        </div>
    </div>
    """

    return """
    <div>
        <button type="submit" class="btn btn-lg btn-light fw-bold border-white button mx-auto" id="button-download" style="display: block;"><i class="fa-solid fa-down-long"></i></button>
        <div class="alert alert-success mx-auto success-card" id="success-card" style="display: none;">
            <strong>Download completed!</strong>
        </div>
    </div>
    """


@app.post(
    '/download/',
    response_class=JSONResponse,
    response_model=Message,
    tags=['Downloader'],
    summary='Download a song or songs from a playlist',
)
async def download(
    url: str,
    spotdlc: Spotdl = Depends(get_spotdl),
):
    """
    You can download a single song or all the songs in a playlist, album, etc.

    - **url**: URL of the song or playlist to download.

    ### Responses

    - `200` - Download successful.
    """
    try:
        songs = spotdlc.search([url])
        # Run download in a separate thread with a longer timeout
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            await asyncio.wait_for(
                loop.run_in_executor(executor, download_songs_sync, spotdlc, songs),
                timeout=120.0  # Increased timeout to 120 seconds
            )
        return {'message': 'Download successful'}
    except asyncio.TimeoutError:
        return JSONResponse(status_code=408, content={'detail': 'Download timed out after 120 seconds'})
    except Exception as error:  # pragma: no cover
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={'detail': str(error)})


@app.get(
    '/list',
    response_class=HTMLResponse,
    tags=['Web UI'],
    summary='List downloaded files',
)
def list_downloads_page(request: Request):
    files = get_downloaded_files()
    return templates.TemplateResponse(
        'list.html', {'request': request, 'files': files}
    )


@app.get(
    '/list-items',
    response_class=HTMLResponse,
    tags=['Web UI'],
    summary='Returns downloaded files to list',
)
def list_items_of_downloads_page():
    files = get_downloaded_files()
    return files