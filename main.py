# main.py
import os
import subprocess
import json
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request
from dotenv import load_dotenv

load_dotenv()

DESCRIPTION = "Download Spotify music with album art and metadata."

class Message(BaseModel):
    message: str = Field(examples=['Download successful'])

app = FastAPI(title='Downtify', version='0.3.2', description=DESCRIPTION)

app.mount('/static', StaticFiles(directory='static'), name='static')
app.mount('/assets', StaticFiles(directory='assets'), name='assets')

if not os.path.exists('/downloads'):
    os.makedirs('/downloads')
app.mount('/downloads', StaticFiles(directory='/downloads'), name='downloads')

templates = Jinja2Templates(directory='templates')

def get_downloaded_files() -> str:
    download_path = '/downloads'
    try:
        files = os.listdir(download_path)
        file_links = [f'<li class="list-group-item"><a href="/downloads/{file}">{file}</a></li>' for file in files]
        return ''.join(file_links) if file_links else '<li class="list-group-item">No files found.</li>'
    except Exception as e:
        return f'<li class="list-group-item text-danger">Error: {str(e)}</li>'

@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse('index.html', {'request': request})

@app.post('/download-web', response_class=HTMLResponse)
def download_web_ui(url: str = Form(...)):
    try:
        result = subprocess.run(
            ['python', 'downloader.py', url],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            raise Exception(f"Downloader script failed: {result.stderr}")
        
        output = json.loads(result.stdout)
        if output.get("status") == "error":
            raise Exception(output.get("message", "Unknown downloader error"))
            
    except Exception as error:
        print(f"Error during download process: {error}")
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

@app.post('/download/', response_class=JSONResponse)
def download(url: str):
    try:
        result = subprocess.run(
            ['python', 'downloader.py', url],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            return JSONResponse(status_code=500, content={'detail': f"Script error: {result.stderr}"})
        
        output = json.loads(result.stdout)
        if output.get("status") == "error":
            return JSONResponse(status_code=400, content={'detail': output.get("message")})
            
        return {'message': 'Download successful'}
    except Exception as error:
        return JSONResponse(status_code=500, content={'detail': str(error)})

@app.get('/list', response_class=HTMLResponse)
def list_downloads_page(request: Request):
    return templates.TemplateResponse('list.html', {'request': request, 'files': get_downloaded_files()})

@app.get('/list-items', response_class=HTMLResponse)
def list_items_of_downloads_page():
    return get_downloaded_files()