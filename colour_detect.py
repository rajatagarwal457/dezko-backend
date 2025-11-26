#!/usr/bin/env python3
import subprocess
import sys
import shutil

def get_dominant_color(video_path):
    """
    Analyze video and return dominant color channel (R, G, or B).
    Returns 'R', 'G', 'B', or None if analysis fails.
    """
    if not shutil.which('ffmpeg'):
        print("Error: ffmpeg not found", file=sys.stderr)
        return None

    # Extract frames and get pixel data using ffmpeg
    # Sample 1 frame per second, scale down for faster processing
    cmd = [
        'ffmpeg',
        '-v', 'error',  # Less verbose
        '-i', video_path,
        '-vf', 'fps=1,scale=160:90',  # Sample rate and resolution
        '-f', 'rawvideo',
        '-pix_fmt', 'rgb24',
        '-'
    ]
    
    try:
        # Increase buffer size limit if needed, though capture_output handles it
        result = subprocess.run(cmd, capture_output=True, check=True)
        pixels = result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error processing video {video_path}: {e}", file=sys.stderr)
        if e.stderr:
            print(f"FFmpeg Error Output:\n{e.stderr.decode('utf-8', errors='replace')}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return None
    
    if not pixels:
        return 'R' # Default if no frames

    # Count RGB values across all pixels
    # pixels is a bytes object, so we can iterate directly or use struct
    # Simple iteration is fine for this scale
    
    r_total = 0
    g_total = 0
    b_total = 0
    
    # Iterate through bytes: R, G, B, R, G, B...
    total_bytes = len(pixels)
    # Ensure we don't go out of bounds if incomplete pixel
    limit = total_bytes - (total_bytes % 3)
    
    for i in range(0, limit, 3):
        r_total += pixels[i]
        g_total += pixels[i+1]
        b_total += pixels[i+2]
    
    # Determine which channel is dominant
    max_value = max(r_total, g_total, b_total)
    
    if max_value == 0:
        return 'R' # Default for black video
        
    if max_value == r_total:
        return 'R'
    elif max_value == g_total:
        return 'G'
    else:
        return 'B'

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <video.mp4>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    dominant = get_dominant_color(video_path)
    if dominant:
        print(dominant)
    else:
        sys.exit(1)