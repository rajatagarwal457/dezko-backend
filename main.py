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
from typing import List

# Import engine from current directory
from engine import BeatSyncEngine

app = FastAPI()
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

# Mount outputs for static access
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

# Available songs - these should have corresponding audio and beats.xml files
AVAILABLE_SONGS = [
    {
        "id": "dezko",
        "name": "Dezko",
        "artist": "Electronic Mix",
        "duration": "0:23",
        "audioFile": "dezko.mp3",
        "beatsFile": "beats.xml"
    },
    # Add more songs here as you create them
    # {
    #     "id": "song2",
    #     "name": "Another Song",
    #     "artist": "Artist Name",
    #     "duration": "4:12",
    #     "audioFile": "song2.mp3",
    #     "beatsFile": "beats_song2.xml"
    # },
]

@app.get("/songs")
async def get_songs():
    """Get list of available songs"""
    return AVAILABLE_SONGS

@app.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    # Create a unique session ID for this batch? 
    # For now, let's just clear uploads or use a specific folder per request if we want multi-user.
    # To keep it simple for this MVP: Just save to uploads/
    
    saved_files = []
    for file in files:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)
        
    return {"message": f"Uploaded {len(saved_files)} files", "files": saved_files}

from fastapi.concurrency import run_in_threadpool

from typing import Optional

class GenerateRequest(BaseModel):
    song_id: Optional[str] = "dezko"

@app.post("/generate")
async def generate_video(request: GenerateRequest, background_tasks: BackgroundTasks):
    try:
        # Find song by ID
        song_id = request.song_id or "dezko"
        song = next((s for s in AVAILABLE_SONGS if s["id"] == song_id), None)
        if not song:
            # Fallback to first song if specific id not found, or raise error
            # For now, let's just default to the first one if "dezko" isn't found for some reason
            if AVAILABLE_SONGS:
                song = AVAILABLE_SONGS[0]
            else:
                raise HTTPException(status_code=400, detail=f"Song with id '{song_id}' not found and no default available")
        
        # Verify audio and beats files exist
        audio_path = os.path.join(current_dir, song["audioFile"])
        beats_path = os.path.join(current_dir, song["beatsFile"])
        
        if not os.path.exists(audio_path):
            raise HTTPException(status_code=404, detail=f"Audio file '{song['audioFile']}' not found")
        if not os.path.exists(beats_path):
            raise HTTPException(status_code=404, detail=f"Beats file '{song['beatsFile']}' not found")
        
        # Initialize engine with current dir (where beats.xml is)
        engine = BeatSyncEngine(current_dir)
        # Override the audio and beats file for this song
        engine.audio_file = audio_path
        engine.beats_file = beats_path
        
        # Output file
        output_filename = f"render_{uuid.uuid4().hex[:8]}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
        # Run in threadpool to avoid blocking the event loop
        print(f"Starting render to {output_path} with song {song['name']}")
        await run_in_threadpool(engine.render, UPLOAD_DIR, output_path)
        
        return {
            "status": "success", 
            "video_url": f"/outputs/{output_filename}",
            "filename": output_filename
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/clear")
async def clear_uploads():
    files = glob.glob(os.path.join(UPLOAD_DIR, "*"))
    for f in files:
        os.remove(f)
    return {"message": "Uploads cleared"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

