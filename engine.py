import xml.etree.ElementTree as ET
import subprocess
import random
import os
import glob
import json
import sys
import shutil
import uuid

class BeatSyncEngine:
    def __init__(self, project_dir, use_gpu=True):
        self.project_dir = project_dir
        self.beats_file = os.path.join(project_dir, 'beats.xml')
        self.audio_file = os.path.join(project_dir, 'dezko.mp3')
        self.output_width = 1080
        self.output_height = 1920
        self.temp_dir = os.path.join(project_dir, 'temp_render')
        self.use_gpu = use_gpu
        
        # Verify GPU availability if requested
        if self.use_gpu:
            self.verify_gpu()
        
    def verify_gpu(self):
        """Check if NVIDIA GPU and encoders are available."""
        try:
            # Check nvidia-smi
            result = subprocess.run(['nvidia-smi'], 
                                  stdout=subprocess.PIPE, 
                                  stderr=subprocess.PIPE,
                                  check=True)
            print("✓ NVIDIA GPU detected")
            
            # Check for nvenc support in ffmpeg
            result = subprocess.run(['ffmpeg', '-encoders'],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  text=True)
            if 'h264_nvenc' in result.stdout:
                print("✓ NVENC encoder available")
            else:
                print("⚠ Warning: NVENC not found, falling back to CPU")
                self.use_gpu = False
                
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("⚠ Warning: GPU not available, falling back to CPU")
            self.use_gpu = False
        
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

    def get_video_info(self, video_path):
        """Get duration, width, and height of video file using ffprobe."""
        cmd = [
            'ffprobe', 
            '-v', 'error', 
            '-show_entries', 'format=duration:stream=width,height', 
            '-of', 'json', 
            video_path
        ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            data = json.loads(result.stdout)
            
            duration = float(data['format']['duration'])
            
            # Find video stream dimensions
            width = 0
            height = 0
            for stream in data.get('streams', []):
                if 'width' in stream and 'height' in stream:
                    width = int(stream['width'])
                    height = int(stream['height'])
                    break
                    
            return {'duration': duration, 'width': width, 'height': height}
        except Exception as e:
            print(f"Error getting info for {video_path}: {e}")
            return {'duration': 0.0, 'width': 0, 'height': 0}

    def normalize_asset(self, input_path, output_path):
        """Normalize input video to standard format using GPU acceleration."""
        print(f"Normalizing {input_path} to {output_path}...")
        
        if self.use_gpu:
            # GPU-accelerated normalization
            # scale_cuda does not support force_original_aspect_ratio, so we calculate dimensions manually
            info = self.get_video_info(input_path)
            in_w = info['width']
            in_h = info['height']
            
            if in_w == 0 or in_h == 0:
                print(f"Warning: Could not get dimensions for {input_path}, falling back to CPU")
                self.use_gpu = False
                # Fallback to CPU logic below
            else:
                # Calculate target dimensions to cover 1080x1920
                target_w = self.output_width
                target_h = self.output_height
                
                scale_x = target_w / in_w
                scale_y = target_h / in_h
                scale_factor = max(scale_x, scale_y)
                
                new_w = int(in_w * scale_factor)
                new_h = int(in_h * scale_factor)
                
                # Ensure even dimensions
                if new_w % 2 != 0: new_w += 1
                if new_h % 2 != 0: new_h += 1
                
                # Calculate crop offsets (center crop)
                crop_x = (new_w - target_w) // 2
                crop_y = (new_h - target_h) // 2
                
                cmd = [
                    'ffmpeg', '-y',
                    '-hwaccel', 'cuda',
                    '-hwaccel_output_format', 'cuda',
                    '-i', input_path,
                    # Pipeline:
                    # 1. Scale on GPU (scale_cuda)
                    # 2. Download to CPU (hwdownload)
                    # 3. Convert format (format=nv12)
                    # 4. FPS conversion (fps=30)
                    # 5. Crop (crop=...)
                    # 6. Set SAR (setsar=1)
                    '-vf', f"scale_cuda={new_w}:{new_h},hwdownload,format=nv12,fps=30,crop={target_w}:{target_h}:{crop_x}:{crop_y},setsar=1",
                    '-c:v', 'h264_nvenc',
                    '-preset', 'p4',
                    '-tune', 'hq',
                    '-rc', 'vbr',
                    '-cq', '19',
                    '-b:v', '0',
                    '-g', '1',
                    output_path
                ]
                subprocess.run(cmd, check=True)
                return

        # CPU fallback (original code)
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vf', f"fps=30,scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=increase,crop={self.output_width}:{self.output_height},format=yuv420p,setsar=1",
            '-c:v', 'libx264',
            '-crf', '18',
            '-preset', 'fast',
            '-g', '1',
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
            '-c', 'copy',  # Fast copy since source is already normalized
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
            print(f"GPU Acceleration: {'ENABLED' if self.use_gpu else 'DISABLED'}")
            
            # Get assets
            asset_files = sorted(glob.glob(os.path.join(assets_dir, "*")))
            valid_extensions = {'.mp4', '.mov', '.avi', '.mkv'}
            asset_files = [f for f in asset_files if os.path.splitext(f)[1].lower() in valid_extensions]
            
            if not asset_files:
                raise ValueError(f"No video assets found in {assets_dir}")
                
            print(f"Found {len(asset_files)} assets.")
            
            # 1. Normalize Assets
            normalized_assets = {}  # { original_path: normalized_path }
            
            for i, asset_path in enumerate(asset_files):
                norm_name = f"norm_{i}.mp4"
                norm_path = os.path.join(self.temp_dir, norm_name)
                self.normalize_asset(asset_path, norm_path)
                
                # Get duration of normalized asset
                info = self.get_video_info(norm_path)
                normalized_assets[asset_path] = {
                    'path': norm_path,
                    'duration': info['duration']
                }

            # 2. Generate Cut List with Smart Selection
            fps = 30
            current_frame = 0
            usage_map = {path: [] for path in normalized_assets}
            clip_files = []  # List of clip filenames for concat
            
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
                '-c:v', 'copy',  # Copy video stream (fast!)
                '-c:a', 'aac',  # Encode audio
                '-map', '0:v',
                '-map', '1:a',
                '-shortest',  # Stop when shortest input ends
                output_file
            ]
            
            # Save command for debugging
            with open(os.path.join(self.project_dir, 'render_cmd.txt'), 'w') as f:
                f.write(" ".join(cmd))
                
            subprocess.run(cmd, check=True)
            print("Render complete!")

        finally:
            # Cleanup
            if os.path.exists(self.temp_dir):
                print("Cleaning up temp files...")
                shutil.rmtree(self.temp_dir)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python engine.py <assets_dir> <output_file> [--no-gpu]")
        sys.exit(1)
        
    assets_dir = sys.argv[1]
    output_file = sys.argv[2]
    use_gpu = '--no-gpu' not in sys.argv
    
    # Assume script is in project-dezko folder
    project_dir = os.path.dirname(os.path.abspath(__file__))
    
    engine = BeatSyncEngine(project_dir, use_gpu=use_gpu)
    engine.render(assets_dir, output_file)