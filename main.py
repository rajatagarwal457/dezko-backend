from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import shutil
import os
import sys
import uuid
import glob
import datetime
from typing import List
from dotenv import load_dotenv
import boto3
import requests
from botocore.config import Config
import logfire

load_dotenv()

# Import engine from current directory
from engine import BeatSyncEngine

app = FastAPI()

def scrubbing_callback(m: logfire.ScrubMatch):
    if m.path == ('attributes', 'fastapi.arguments.values', 'request', 'sessionId'):
        return m.value

logfire.configure(scrubbing=logfire.ScrubbingOptions(callback=scrubbing_callback))
logfire.instrument_fastapi(app, excluded_urls="/health")

current_dir = os.getcwd()
# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
UPLOAD_DIR = os.path.join(current_dir, "uploads")
OUTPUT_DIR = os.path.join(current_dir, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
BUCKET_NAME = os.getenv("BUCKET_NAME")
# Mount outputs for static access
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

# Available songs - these should have corresponding audio and beats.xml files
AVAILABLE_SONGS = [
    {
        "vibe": "hardcore",
        "duration": "0:23",
        "audioFile": "hardcore.mp3",
        "beatsFile": "hardcore.xml"
    },
    {
        "vibe": "party",
        "duration": "0:13",
        "audioFile": "party.mp3",
        "beatsFile": "party.xml"
    },
    {
        "vibe": "cute",
        "duration": "0:23",
        "audioFile": "cute.mp3",
        "beatsFile": "cute.xml"
    },
    {
        "vibe": "nostalgia",
        "duration": "0:14",
        "audioFile": "nostalgia.mp3",
        "beatsFile": "nostalgia.xml"
    },
    {
        "vibe": "lovey-dovey",
        "duration": "0:08",
        "audioFile": "lovey-dovey.mp3",
        "beatsFile": "lovey-dovey.xml"
    },
    {
        "vibe": "2025-throwback",
        "duration": "0:13",
        "audioFile": "2025-throwback.mp3",
        "beatsFile": "2025-throwback.xml"
    },
]

@app.get("/songs")
async def get_songs():
    """Get list of available songs"""
    return AVAILABLE_SONGS

@app.get("/health")
async def health_check():
    return {"status": "ok"}



from fastapi.concurrency import run_in_threadpool

from typing import Optional

class GenerateRequest(BaseModel):
    song_id: Optional[str] = "dezko"
    sessionId: str
    fileNames: List[str]
    vibe: str

class UploadRequest(BaseModel):
    sessionId: str
    filename: str
    content_type: str

@app.post("/get-upload-url")
async def get_upload_url(request: UploadRequest):
    client_config = Config(
        s3={'use_accelerate_endpoint': True}
    )

    s3_client = boto3.client('s3', config=client_config)
    key = f"uploads/{request.sessionId}/{request.filename}"
    
    url = s3_client.generate_presigned_url(
        'put_object',
        Params={'Bucket': BUCKET_NAME, 'Key': key, 'ContentType': request.content_type},
        ExpiresIn=3600
    )
    return {"upload_url": url, "key": key}

@app.post("/generate")
async def generate_video(request: GenerateRequest, background_tasks: BackgroundTasks):
    try:
        match(request.vibe):
            case "hardcore":
                audio_path = os.path.join(current_dir, "audio", "hardcore.mp3")
                beats_path = os.path.join(current_dir, "beats", "hardcore.xml")
            case "party":
                audio_path = os.path.join(current_dir, "audio", "party.mp3")
                beats_path = os.path.join(current_dir, "beats", "party.xml")
            case "cute":
                audio_path = os.path.join(current_dir, "audio", "cute.mp3")
                beats_path = os.path.join(current_dir, "beats", "cute.xml")
            case "nostalgia":
                audio_path = os.path.join(current_dir, "audio", "nostalgia.mp3")
                beats_path = os.path.join(current_dir, "beats", "nostalgia.xml")
            case "lovey-dovey":
                audio_path = os.path.join(current_dir, "audio", "lovey-dovey.mp3")
                beats_path = os.path.join(current_dir, "beats", "lovey-dovey.xml")
            case "2025-throwback":
                audio_path = os.path.join(current_dir, "audio", "2025-throwback.mp3")
                beats_path = os.path.join(current_dir, "beats", "2025-throwback.xml")
            case _:
                raise HTTPException(status_code=400, detail="Invalid vibe")

        
        # if not os.path.exists(audio_path):
        #     raise HTTPException(status_code=404, detail=f"Audio file '{song['audioFile']}' not found")
        # if not os.path.exists(beats_path):
        #     raise HTTPException(status_code=404, detail=f"Beats file '{song['beatsFile']}' not found")
        
        # Initialize engine with current dir (where beats.xml is)
        engine = BeatSyncEngine(current_dir)
        # Override the audio and beats file for this song
        engine.audio_file = audio_path
        engine.beats_file = beats_path
        upload_path = request.sessionId
        os.mkdir(os.path.join(UPLOAD_DIR, upload_path))
        # download raw footage from s3 via cloudfront
        for file in request.fileNames:
            try:
                print("Downloading", file)
                url = f"https://dsfvy2cdoas23.cloudfront.net/uploads/{request.sessionId}/{file}"
                print("Downloading from", url)
                response = requests.get(url)
                with open(os.path.join(UPLOAD_DIR, upload_path, file), "wb") as f:
                    f.write(response.content)
                    print("Downloaded", file)
            except Exception as e:
                print("Failed to download", file, e)

        # Verify session directory exists
        session_dir = os.path.join(UPLOAD_DIR, request.sessionId)
        if not os.path.exists(session_dir):
            raise HTTPException(status_code=404, detail="Session not found or expired")

        # Output file
        output_filename = f"render_{uuid.uuid4().hex[:8]}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
        # Add render task to background
        # print(f"Queuing render to {output_path} with song {song['name']} for session {request.sessionId}")
        background_tasks.add_task(engine.render, session_dir, output_path)
        
        return {
            "status": "generating", 
            "video_url": f"https://dsfvy2cdoas23.cloudfront.net/videos/{output_filename}",
            "filename": output_filename
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=404, detail=f"{str(e)}")

@app.delete("/clear")
async def clear_uploads():
    files = glob.glob(os.path.join(UPLOAD_DIR, "*"))
    for f in files:
        # Don't delete directories (session folders) yet, or handle recursively
        if os.path.isfile(f):
            os.remove(f)
    return {"message": "Uploads cleared"}

class ErrorLog(BaseModel):
    error: str

@app.post("/error")
async def log_error(error_log: ErrorLog):
    try:
        errors_dir = os.path.join(current_dir, "errors")
        os.makedirs(errors_dir, exist_ok=True)
        
        log_file = os.path.join(errors_dir, "error_log.txt")
        
        timestamp = datetime.datetime.now().isoformat()
        log_entry = f"[{timestamp}] {error_log.error}\n"
        
        with open(log_file, "a") as f:
            f.write(log_entry)
            
        return {"message": "Error logged successfully"}
    except Exception as e:
        print(f"Failed to log error: {e}")
        raise HTTPException(status_code=500, detail="Failed to log error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

