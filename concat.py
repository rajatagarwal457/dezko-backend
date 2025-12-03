import os

def save_flist(files):
    f_data = 'file \'' + '\'\nfile \''.join(files) + '\''
    print(f_data)

    f_list = 'list.txt'
    with open(f_list, 'w', encoding='gbk') as f:
        f.write(f_data)
    return f_list

video_path = os.getcwd()
os.chdir(video_path)

output_path = 'output.mp4'

files = ['render.mp4', 'Vireo.mp4']
print(files)        # your video_names.

f_list = save_flist(files)

call = f'ffmpeg -f concat -safe 0 -i list.txt -c:v libx264 -c:a aac -b:a 192k output.mp4'         # only supporte the same video_format, it's very fast because no need recode.

# call = f'ffmpeg -f concat -safe 0 -i {f_list} -vcodec h264_nvenc output.mp4 -y'    # cuda accelerate.

print(call)

os.system(call)

# os.remove(f_list)