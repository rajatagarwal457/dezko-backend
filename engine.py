import xml.etree.ElementTree as ET
import subprocess
import random
import os
import glob
import json
import sys

class BeatSyncEngine:
    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.beats_file = os.path.join(project_dir, 'beats.xml')
        self.audio_file = os.path.join(project_dir, 'dezko.mp3')
        self.output_width = 1080
        self.output_height = 1920
        
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

    def render(self, assets_dir, output_file):
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
        
        # Get asset durations
        assets = []
        for f in asset_files:
            dur = self.get_video_duration(f)
            if dur > 0:
                assets.append({'path': f, 'duration': dur})
            else:
                print(f"Skipping invalid asset: {f}")
        
        if not assets:
            raise ValueError("No valid assets available.")

        # Assign assets to cuts
        filter_complex = []
        inputs = []
        
        for i, asset in enumerate(assets):
            inputs.extend(['-i', asset['path']])
            
        # Add audio input (last input)
        inputs.extend(['-i', self.audio_file])
        audio_index = len(assets)
        
        concat_inputs = []
        
        # SYNC FIX: Quantize everything to 30fps frames
        fps = 30
        current_frame = 0
        
        # SMART ASSET SELECTION
        # Track usage: { asset_index: [(start_sec, end_sec), ...] }
        usage_map = {i: [] for i in range(len(assets))}
        
        print("Generating cut list with Smart Asset Selection...")
        for i, cut in enumerate(cuts):
            # Calculate duration in frames
            target_end_time = cut['start'] + cut['duration']
            target_end_frame = int(target_end_time * fps)
            duration_frames = target_end_frame - current_frame
            current_frame = target_end_frame
            
            if duration_frames <= 0:
                continue
                
            duration_sec = duration_frames / fps
            
            # Find an asset and a time slot
            # Strategy:
            # 1. Shuffle assets to randomize selection
            # 2. For each asset, try to find a random valid slot that doesn't overlap with existing usage
            # 3. If no non-overlapping slot found in ANY asset, pick a random one and allow overlap
            
            candidate_indices = list(range(len(assets)))
            random.shuffle(candidate_indices)
            
            selected_asset_idx = -1
            selected_start_time = -1
            
            # Try to find a clean slot
            for idx in candidate_indices:
                asset = assets[idx]
                max_start = asset['duration'] - duration_sec - 0.1
                if max_start <= 0: continue
                
                # Try N times to find a slot in this asset
                for _ in range(10):
                    t = random.uniform(0, max_start)
                    t_end = t + duration_sec
                    
                    # Check overlap
                    overlaps = False
                    for (u_start, u_end) in usage_map[idx]:
                        # Check intersection: (StartA <= EndB) and (EndA >= StartB)
                        if (t <= u_end) and (t_end >= u_start):
                            overlaps = True
                            break
                    
                    if not overlaps:
                        selected_asset_idx = idx
                        selected_start_time = t
                        break
                
                if selected_asset_idx != -1:
                    break
            
            # Fallback: If we couldn't find a clean slot in ANY asset
            if selected_asset_idx == -1:
                print(f"Warning: Could not find non-overlapping slot for cut {i}. Reusing content.")
                selected_asset_idx = random.choice(candidate_indices)
                asset = assets[selected_asset_idx]
                max_start = asset['duration'] - duration_sec - 0.1
                selected_start_time = random.uniform(0, max(0, max_start))
            
            # Record usage
            usage_map[selected_asset_idx].append((selected_start_time, selected_start_time + duration_sec))
            
            # Calculate start/end frames
            start_frame = int(selected_start_time * fps)
            end_frame = start_frame + duration_frames
            
            # Create filter chain
            filter_cmd = (
                f"[{selected_asset_idx}:v]"
                f"fps={fps},"
                f"scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=increase,"
                f"crop={self.output_width}:{self.output_height},"
                f"format=yuv420p,"
                f"setsar=1,"
                f"trim=start_frame={start_frame}:end_frame={end_frame},"
                f"setpts=PTS-STARTPTS"
                f"[v{i}]"
            )
            filter_complex.append(filter_cmd)
            concat_inputs.append(f"[v{i}]")
            
        # Concat filter
        concat_cmd = f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=0[outv]"
        filter_complex.append(concat_cmd)
        
        # Full command
        cmd = ['ffmpeg', '-y']
        cmd.extend(inputs)
        cmd.extend(['-filter_complex', ";".join(filter_complex)])
        cmd.extend(['-map', '[outv]', '-map', f'{audio_index}:a'])
        # Force output frame rate
        cmd.extend(['-r', str(fps)])
        cmd.extend(['-c:v', 'libx264', '-pix_fmt', 'yuv420p'])
        cmd.append(output_file)
        
        print(f"Rendering {len(concat_inputs)} cuts to {output_file}...")
        
        with open(os.path.join(self.project_dir, 'render_cmd.txt'), 'w') as f:
            f.write(" ".join(cmd))
            
        subprocess.run(cmd, check=True)
        print("Render complete!")

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
