import xml.etree.ElementTree as ET
import subprocess
import random
import os
import glob
import json
import sys
import shutil
import uuid
import boto3
from dotenv import load_dotenv

load_dotenv()

class BeatSyncEngine:
    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.beats_file = os.path.join(project_dir, 'beats.xml')
        self.audio_file = os.path.join(project_dir, 'dezko.mp3')
        self.output_width = 1080
        self.output_height = 1920
        self.temp_dir = os.path.join(project_dir, 'temp_render')
        
    def parse_beats(self):
        """Parse beats.xml to get list of (start_time, duration) for each cut."""
        tree = ET.parse(self.beats_file)
        root = tree.getroot()
        
        beats = []
        for beat in root.findall('.//Beat'):
            beats.append({
                'index': int(beat.get('index')),
                'time': float(beat.get('time'))
            })
            
        # Sort just in case
        beats.sort(key=lambda x: x['time'])
        
        # Calculate durations
        total_duration = float(root.find('.//Duration').text)
        
        cuts = []
        for i in range(len(beats)):
            start = beats[i]['time']
            if i < len(beats) - 1:
                end = beats[i+1]['time']
            else:
                end = total_duration
            
            duration = end - start
            cuts.append({
                'index': beats[i]['index'],
                'start': start,
                'duration': duration
            })
            
        return cuts, total_duration

    def get_video_duration(self, video_path):
        """Get duration of video file using ffprobe."""
        cmd = [
            'ffprobe', 
            '-v', 'error', 
            '-show_entries', 'format=duration', 
            '-of', 'default=noprint_wrappers=1:nokey=1', 
            video_path
        ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return float(result.stdout.strip())
        except Exception as e:
            print(f"Error getting duration for {video_path}: {e}")
            return 0.0

    def normalize_asset(self, input_path, output_path):
        """Normalize input video to standard format using GPU acceleration."""
        print(f"Normalizing {input_path} to {output_path}...")
        cmd = [
            'ffmpeg', '-y',
            '-hwaccel', 'cuda',
            '-i', input_path,
            '-vf', f"fps=30,scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=increase,crop={self.output_width}:{self.output_height},format=yuv420p,setsar=1",
            '-c:v', 'h264_nvenc',
            '-preset', 'p4',
            '-cq', '19',
            '-g', '1',
            '-bf', '0',  # Disable B-frames for All-Intra
            output_path
        ]
        subprocess.run(cmd, check=True)

    def create_clip(self, source_path, start_time, duration_frames, output_path):
        """Extract a clip from the normalized source."""
        # Use -frames:v for exact frame count extraction
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_time),
            '-i', source_path,
            '-frames:v', str(duration_frames),
            '-c', 'copy', # Fast copy since source is already normalized
            output_path
        ]
        subprocess.run(cmd, check=True)

    def render(self, assets_dir, output_file):
        # Create temp dir
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        os.makedirs(self.temp_dir)

        try:
            print(f"Parsing beats from {self.beats_file}...")
            cuts, total_duration = self.parse_beats()
            print(f"Found {len(cuts)} cuts. Total duration: {total_duration:.2f}s")
            
            # Get assets
            asset_files = sorted(glob.glob(os.path.join(assets_dir, "*")))
            valid_extensions = {'.mp4', '.mov', '.avi', '.mkv'}
            asset_files = [f for f in asset_files if os.path.splitext(f)[1].lower() in valid_extensions]
            
            if not asset_files:
                raise ValueError(f"No video assets found in {assets_dir}")
                
            print(f"Found {len(asset_files)} assets.")
            
            # 1. Normalize Assets
            # We only need to normalize assets that we actually use, but for simplicity and random access,
            # let's normalize all of them or do it on demand. 
            # To save time, let's normalize all found assets first.
            
            normalized_assets = {} # { original_path: normalized_path }
            
            for i, asset_path in enumerate(asset_files):
                norm_name = f"norm_{i}.mp4"
                norm_path = os.path.join(self.temp_dir, norm_name)
                self.normalize_asset(asset_path, norm_path)
                normalized_assets[asset_path] = {
                    'path': norm_path,
                    'duration': self.get_video_duration(norm_path)
                }

            # 2. Generate Cut List with Smart Selection
            # This logic is similar to previous, but now we select from normalized assets
            
            fps = 30
            current_frame = 0
            usage_map = {path: [] for path in normalized_assets}
            clip_files = [] # List of clip filenames for concat
            previous_asset_path = None  # Track the previously used asset to avoid consecutive clips from same video
            
            print("Generating clips...")
            
            for i, cut in enumerate(cuts):
                # Calculate duration in frames
                target_end_time = cut['start'] + cut['duration']
                target_end_frame = int(target_end_time * fps)
                duration_frames = target_end_frame - current_frame
                current_frame = target_end_frame
                
                if duration_frames <= 0:
                    continue
                    
                duration_sec = duration_frames / fps
                
                # Find an asset and time slot
                candidate_paths = list(normalized_assets.keys())
                
                # Exclude the previously used asset to avoid back-to-back clips from same video
                if previous_asset_path and previous_asset_path in candidate_paths and len(candidate_paths) > 1:
                    candidate_paths.remove(previous_asset_path)
                
                random.shuffle(candidate_paths)
                
                selected_asset_path = None
                selected_start_time = -1
                
                # Try to find a clean slot
                for path in candidate_paths:
                    asset_info = normalized_assets[path]
                    max_start = asset_info['duration'] - duration_sec - 0.1
                    if max_start <= 0: continue
                    
                    for _ in range(10):
                        t = random.uniform(0, max_start)
                        # Align t to frame boundary
                        t_frame = int(t * fps)
                        t = t_frame / fps
                        
                        t_end = t + duration_sec
                        
                        overlaps = False
                        for (u_start, u_end) in usage_map[path]:
                            if (t <= u_end) and (t_end >= u_start):
                                overlaps = True
                                break
                        
                        if not overlaps:
                            selected_asset_path = path
                            selected_start_time = t
                            break
                    
                    if selected_asset_path:
                        break
                
                # Fallback
                if not selected_asset_path:
                    print(f"Warning: Could not find non-overlapping slot for cut {i}. Reusing content.")
                    selected_asset_path = random.choice(candidate_paths)
                    asset_info = normalized_assets[selected_asset_path]
                    max_start = asset_info['duration'] - duration_sec - 0.1
                    # Align random start to frame
                    t = random.uniform(0, max(0, max_start))
                    t_frame = int(t * fps)
                    selected_start_time = t_frame / fps
                
                # Record usage
                usage_map[selected_asset_path].append((selected_start_time, selected_start_time + duration_sec))
                
                # Update previous_asset_path to track for next iteration
                previous_asset_path = selected_asset_path
                
                # Create Clip
                clip_name = f"clip_{i:04d}.mp4"
                clip_path = os.path.join(self.temp_dir, clip_name)
                print(f"Creating clip {i+1}/{len(cuts)}: {clip_name} from {os.path.basename(selected_asset_path)} at {selected_start_time:.2f}s ({duration_frames} frames)")
                self.create_clip(normalized_assets[selected_asset_path]['path'], selected_start_time, duration_frames, clip_path)
                clip_files.append(clip_path)

            # 3. Concat Clips
            concat_list_path = os.path.join(self.temp_dir, 'clips.txt')
            with open(concat_list_path, 'w') as f:
                for clip_path in clip_files:
                    # FFmpeg concat requires absolute paths or relative safe paths. 
                    # Escaping backslashes for Windows might be needed.
                    safe_path = clip_path.replace('\\', '/')
                    f.write(f"file '{safe_path}'\n")
            
            print(f"Concatenating {len(clip_files)} clips...")
            
            # Final concat command
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_list_path,
                '-i', self.audio_file,
                '-c:v', 'copy', # Copy video stream (fast!)
                '-c:a', 'aac',  # Encode audio
                '-map', '0:v',
                '-map', '1:a',
                '-shortest', # Stop when shortest input ends (video should match audio roughly)
                output_file
            ]
            
            # Save command for debugging
            with open(os.path.join(self.project_dir, 'render_cmd.txt'), 'w') as f:
                f.write(" ".join(cmd))
            subprocess.run(cmd, check=True)
                        
            # Append vireo.mp4 to the end of the rendered video
            vireo_path = os.path.join(self.project_dir, 'vireo.mp4')
            if os.path.exists(vireo_path):
                print("Appending vireo.mp4 to the end of the video...")
                temp_output = output_file.replace('.mp4', '_temp.mp4')
                
                # Create concat list
                concat_final_path = os.path.join(self.temp_dir, 'final_concat.txt')
                with open(concat_final_path, 'w') as f:
                    f.write(f"file '{output_file.replace(chr(92), '/')}'\n")
                    f.write(f"file '{vireo_path.replace(chr(92), '/')}'\n")
                
                # Concatenate the rendered video with vireo.mp4
                concat_cmd = [
                     'ffmpeg', '-y',
                     '-i', output_file,  # Regenerate presentation timestamps
                     '-i', vireo_path,
                     '-filter-complex', '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]',
                     '-map', '[outv]',
                     '-map', '[outa]',
                     '-c:v', 'h264_nvenc',
                     '-c:a', 'aac',
                     temp_output
                ]
                subprocess.run(concat_cmd, check=True)
                
                # Replace original with concatenated version
                os.remove(output_file)
                os.rename(temp_output, output_file)
                print("Successfully appended vireo.mp4!")
            else:
                print(f"Warning: vireo.mp4 not found at {vireo_path}, skipping append.")
            s3 = boto3.client('s3')
            s3.upload_file(output_file, 'dezko', f"videos/{os.path.basename(output_file)}")
            print("Render complete!")

        finally:
            # Cleanup
            if os.path.exists(self.temp_dir):
                print("Cleaning up temp files...")
                shutil.rmtree(self.temp_dir)
            if os.path.exists(assets_dir):
                print("Cleaning up assets...")
                shutil.rmtree(assets_dir)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python engine.py <assets_dir> <output_file>")
        sys.exit(1)
        
    assets_dir = sys.argv[1]
    output_file = sys.argv[2]
    
    # Assume script is in project-dezko folder
    project_dir = os.path.dirname(os.path.abspath(__file__))
    
    engine = BeatSyncEngine(project_dir)
    engine.render(assets_dir, output_file)