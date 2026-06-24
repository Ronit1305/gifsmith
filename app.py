import os
import uuid
import subprocess
import threading
import time
import json
import random
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename
from PIL import Image, ImageSequence

app = Flask(__name__)

UPLOAD_FOLDER = '/tmp/video2gif_uploads'
OUTPUT_FOLDER = '/tmp/video2gif_outputs'
MAX_CONTENT_LENGTH = 500 * 1024 * 1024

app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'm4v', 'wmv', '3gp', 'ts'}

jobs = {}
jobs_lock = threading.Lock()
JOBS_FILE = '/tmp/video2gif_jobs.json'


def save_jobs():
    try:
        full = {k: dict(v) for k, v in jobs.items()}
        with open(JOBS_FILE, 'w') as f:
            json.dump(full, f)
    except Exception:
        pass


def load_jobs():
    try:
        if os.path.exists(JOBS_FILE):
            with open(JOBS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


jobs = load_jobs()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def cleanup_old_files():
    while True:
        time.sleep(300)
        now = time.time()
        for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
            for f in os.listdir(folder):
                fpath = os.path.join(folder, f)
                try:
                    if now - os.path.getmtime(fpath) > 3600:
                        os.remove(fpath)
                except Exception:
                    pass
        with jobs_lock:
            to_del = [jid for jid, j in jobs.items()
                      if now - j.get('created_at', now) > 3600]
            for jid in to_del:
                del jobs[jid]
            save_jobs()


threading.Thread(target=cleanup_old_files, daemon=True).start()


def get_video_info(video_path):
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', '-show_format', video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    info = {'duration': 0, 'width': 0, 'height': 0}
    try:
        info['duration'] = float(data['format']['duration'])
    except Exception:
        pass
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video':
            info['width'] = stream.get('width', 0)
            info['height'] = stream.get('height', 0)
            break
    return info


def build_ffmpeg_command(input_path, output_path, options):
    fps = int(options.get('fps', 15))
    width = int(options.get('width', 480))
    height = int(options.get('height', -1))
    start_time = float(options.get('start_time', 0))
    end_time = options.get('end_time')

    # Properly handle -1 (auto) on either dimension
    if width == -1 or width == 0:
        scale = f"scale=-2:{height}:flags=lanczos"
    elif height == -1 or height == 0:
        scale = f"scale={width}:-2:flags=lanczos"
    else:
        scale = f"scale={width}:{height}:flags=lanczos:force_original_aspect_ratio=decrease"

    palette_path = output_path.replace('.gif', '_palette.png')

    extra_input = ['-ss', str(start_time)]
    if end_time:
        duration = float(end_time) - start_time
        extra_input += ['-t', str(duration)]

    # setparams retags Apple HEVC color space as bt709 so palettegen
    # doesn't fail with "not in sRGB" on iPhone .mov files
    color_fix = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709"

    vf_pass1 = f"fps={fps},{scale},{color_fix},format=rgb24,palettegen=stats_mode=diff:max_colors=256"
    vf_pass2 = f"fps={fps},{scale},{color_fix},format=rgb24"

    pass1 = (
        ['ffmpeg', '-y'] +
        extra_input +
        ['-i', input_path,
         '-vf', vf_pass1,
         '-frames:v', '1',
         palette_path]
    )

    pass2 = (
        ['ffmpeg', '-y'] +
        extra_input +
        ['-i', input_path,
         '-i', palette_path,
         '-lavfi', f'{vf_pass2} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle',
         output_path]
    )

    return pass1, pass2, palette_path


def build_mp4_command(input_path, output_path, options):
    fps = int(options.get('fps', 30))
    width = int(options.get('width', 480))
    height = int(options.get('height', -1))
    start_time = float(options.get('start_time', 0))
    end_time = options.get('end_time')

    if width == -1 or width == 0:
        scale = f"scale=-2:{height}:flags=lanczos"
    elif height == -1 or height == 0:
        scale = f"scale={width}:-2:flags=lanczos"
    else:
        scale = f"scale={width}:{height}:flags=lanczos:force_original_aspect_ratio=decrease"

    extra_input = ['-ss', str(start_time)]
    if end_time:
        duration = float(end_time) - start_time
        extra_input += ['-t', str(duration)]

    cmd = (
        ['ffmpeg', '-y'] +
        extra_input +
        ['-i', input_path,
         '-vf', f'fps={fps},{scale}',
         '-c:v', 'libx264',
         '-crf', '18',
         '-preset', 'fast',
         '-pix_fmt', 'yuv420p',
         '-an',
         '-movflags', '+faststart',
         output_path]
    )
    return cmd


def add_unique_noise(gif_path):
    img = Image.open(gif_path)
    frames = []
    durations = []

    for frame in ImageSequence.Iterator(img):
        duration = frame.info.get('duration', 50)
        durations.append(duration)
        f = frame.convert('RGBA')
        pixels = f.load()
        w, h = f.size
        num_pixels = max(5, min(30, int(w * h * 0.0001)))
        for _ in range(num_pixels):
            x = random.randint(0, w - 1)
            y = random.randint(0, h - 1)
            r, g, b, a = pixels[x, y]
            channel = random.randint(0, 2)
            delta = random.choice([-1, 1])
            if channel == 0:
                r = max(0, min(255, r + delta))
            elif channel == 1:
                g = max(0, min(255, g + delta))
            else:
                b = max(0, min(255, b + delta))
            pixels[x, y] = (r, g, b, a)
        frames.append(f.convert('P', palette=Image.ADAPTIVE, colors=256))

    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=durations,
        optimize=False
    )


def convert_job(job_id, input_path, output_path, options):
    with jobs_lock:
        jobs[job_id]['status'] = 'processing'
        jobs[job_id]['progress'] = 5
        save_jobs()

    try:
        pass1, pass2, palette_path = build_ffmpeg_command(input_path, output_path, options)

        with jobs_lock:
            jobs[job_id]['progress'] = 20
            jobs[job_id]['stage'] = 'Generating color palette...'
            save_jobs()

        result1 = subprocess.run(pass1, capture_output=True, text=True)
        if result1.returncode != 0:
            raise RuntimeError(f"Palette generation failed: {result1.stderr[-2000:]}")

        with jobs_lock:
            jobs[job_id]['progress'] = 50
            jobs[job_id]['stage'] = 'Converting to GIF...'
            save_jobs()

        result2 = subprocess.run(pass2, capture_output=True, text=True)
        if result2.returncode != 0:
            raise RuntimeError(f"GIF conversion failed: {result2.stderr[-2000:]}")

        try:
            os.remove(palette_path)
        except Exception:
            pass

        with jobs_lock:
            jobs[job_id]['progress'] = 90
            jobs[job_id]['stage'] = 'Finalising...'
            save_jobs()

        add_unique_noise(output_path)

        file_size = os.path.getsize(output_path)

        with jobs_lock:
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['progress'] = 100
            jobs[job_id]['stage'] = 'Done!'
            jobs[job_id]['output_path'] = output_path
            jobs[job_id]['file_size'] = file_size
            save_jobs()

    except Exception as e:
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = str(e)
            save_jobs()
    finally:
        try:
            os.remove(input_path)
        except Exception:
            pass


def mp4_job(job_id, input_path, output_path, options):
    with jobs_lock:
        jobs[job_id]['status'] = 'processing'
        jobs[job_id]['progress'] = 10
        jobs[job_id]['stage'] = 'Converting to MP4...'
        save_jobs()

    try:
        cmd = build_mp4_command(input_path, output_path, options)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"MP4 conversion failed: {result.stderr[-2000:]}")

        file_size = os.path.getsize(output_path)

        with jobs_lock:
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['progress'] = 100
            jobs[job_id]['stage'] = 'Done!'
            jobs[job_id]['output_path'] = output_path
            jobs[job_id]['file_size'] = file_size
            jobs[job_id]['format'] = 'mp4'
            save_jobs()

    except Exception as e:
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = str(e)
            save_jobs()
    finally:
        try:
            os.remove(input_path)
        except Exception:
            pass


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/probe', methods=['POST'])
def probe_video():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    if not allowed_file(f.filename):
        return jsonify({'error': 'Invalid file type'}), 400
    tmp_path = os.path.join(UPLOAD_FOLDER, f'probe_{uuid.uuid4().hex}.tmp')
    f.save(tmp_path)
    info = get_video_info(tmp_path)
    os.remove(tmp_path)
    if not info:
        return jsonify({'error': 'Could not read video info'}), 400
    return jsonify(info)


@app.route('/api/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'error': 'Invalid or missing file'}), 400

    options = {
        'fps': request.form.get('fps', 15),
        'width': request.form.get('width', 480),
        'height': request.form.get('height', -1),
        'start_time': request.form.get('start_time', 0),
        'end_time': request.form.get('end_time') or None,
    }

    job_id = uuid.uuid4().hex
    ext = secure_filename(f.filename).rsplit('.', 1)[1].lower()
    input_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.{ext}')
    output_path = os.path.join(OUTPUT_FOLDER, f'{job_id}.gif')

    f.save(input_path)

    with jobs_lock:
        jobs[job_id] = {
            'status': 'queued',
            'progress': 0,
            'stage': 'Queued...',
            'created_at': time.time(),
        }
        save_jobs()

    thread = threading.Thread(
        target=convert_job,
        args=(job_id, input_path, output_path, options),
        daemon=True
    )
    thread.start()

    return jsonify({'job_id': job_id})


@app.route('/api/convert-mp4', methods=['POST'])
def convert_mp4():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'error': 'Invalid or missing file'}), 400

    options = {
        'fps': request.form.get('fps', 30),
        'width': request.form.get('width', 480),
        'height': request.form.get('height', -1),
        'start_time': request.form.get('start_time', 0),
        'end_time': request.form.get('end_time') or None,
    }

    job_id = uuid.uuid4().hex
    ext = secure_filename(f.filename).rsplit('.', 1)[1].lower()
    input_path = os.path.join(UPLOAD_FOLDER, f'{job_id}.{ext}')
    output_path = os.path.join(OUTPUT_FOLDER, f'{job_id}.mp4')

    f.save(input_path)

    with jobs_lock:
        jobs[job_id] = {
            'status': 'queued',
            'progress': 0,
            'stage': 'Queued...',
            'created_at': time.time(),
            'format': 'mp4',
        }
        save_jobs()

    thread = threading.Thread(
        target=mp4_job,
        args=(job_id, input_path, output_path, options),
        daemon=True
    )
    thread.start()

    return jsonify({'job_id': job_id})


@app.route('/api/status/<job_id>')
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        reloaded = load_jobs()
        job = reloaded.get(job_id)
        if job:
            with jobs_lock:
                jobs[job_id] = job
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({k: v for k, v in job.items() if k != 'output_path'})


@app.route('/api/download/<job_id>')
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        reloaded = load_jobs()
        job = reloaded.get(job_id)
    if not job or job.get('status') != 'done':
        return jsonify({'error': 'Not ready'}), 404
    output_path = job.get('output_path')
    if not output_path or not os.path.exists(output_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(output_path, mimetype='image/gif',
                     as_attachment=True, download_name='converted.gif')


@app.route('/api/download-mp4/<job_id>')
def download_mp4(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        reloaded = load_jobs()
        job = reloaded.get(job_id)
    if not job or job.get('status') != 'done':
        return jsonify({'error': 'Not ready'}), 404
    output_path = job.get('output_path')
    if not output_path or not os.path.exists(output_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(output_path, mimetype='video/mp4',
                     as_attachment=True, download_name='reddit_clip.mp4')


@app.route('/api/preview/<job_id>')
def preview(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        reloaded = load_jobs()
        job = reloaded.get(job_id)
    if not job or job.get('status') != 'done':
        return jsonify({'error': 'Not ready'}), 404
    output_path = job.get('output_path')
    if not output_path or not os.path.exists(output_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(output_path, mimetype='image/gif')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)