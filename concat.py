from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.compositing.CompositeVideoClip import concatenate_videoclips

def concatenate(video_clip_paths, output_path, method="compose"):
    """Concatenates several video files into one video file
    and save it to `output_path`. Note that extension (mp4, etc.) must be added to `output_path`
    `method` can be either 'compose' or 'reduce':
        `reduce`: Reduce the quality of the video to the lowest quality on the list of `video_clip_paths`.
        `compose`: type help(concatenate_videoclips) for the info"""
    # create VideoFileClip object for each video file
    clips = [VideoFileClip(c) for c in video_clip_paths]
    
    # Get the dimensions of the first clip to use as target
    target_width = clips[0].w
    target_height = clips[0].h
    
    # Resize all clips to match the first clip's dimensions
    resized_clips = []
    for clip in clips:
        if clip.w != target_width or clip.h != target_height:
            # Resize to target dimensions
            resized_clip = clip.resized(width=target_width, height=target_height)
            resized_clips.append(resized_clip)
        else:
            resized_clips.append(clip)
    
    final_clip = concatenate_videoclips(resized_clips, method=method)
    # write the output video file
    final_clip.write_videofile(output_path)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Simple Video Concatenation script in Python with MoviePy Library")
    parser.add_argument("-c", "--clips", nargs="+",
                        help="List of audio or video clip paths")
    parser.add_argument("-r", "--reduce", action="store_true", 
                        help="Whether to use the `reduce` method to reduce to the lowest quality on the resulting clip")
    parser.add_argument("-o", "--output", help="Output file name")
    args = parser.parse_args()
    clips = args.clips
    output_path = args.output
    method = "reduce" if args.reduce else "compose"
    concatenate(clips, output_path, method)