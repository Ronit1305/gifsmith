# GIFsmith — Video to GIF Converter

High-quality video-to-GIF converter using FFmpeg's two-pass palettegen/paletteuse pipeline.

## Features
- **Two-pass quality**: palettegen (diff mode) + paletteuse (bayer dithering) for best-in-class GIF quality
- **Reddit Config preset**: 9:16 portrait, 480×854, 24fps — perfect for vertical mobile clips
- **Trim support**: convert specific segments, not the whole video
- **Custom FPS + dimensions**: full control over output size
- **Auto cleanup**: uploads and GIFs deleted after 30 minutes
- **Zero dependencies beyond FFmpeg**: pure stdlib + Flask

## Supported input formats
mp4, mov, mkv, webm, avi, flv, m4v, wmv, 3gp, ts

---

## Deploy to Railway

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/video2gif.git
git push -u origin main
```

### 2. Create Railway project
1. Go to [railway.app](https://railway.app) and sign in
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `video2gif` repo
4. Railway auto-detects the Dockerfile and builds it

### 3. Configure environment (optional)
In Railway dashboard → your service → **Variables**, you can set:
- `PORT` — Railway sets this automatically, no need to change
- No other env vars required

### 4. Set upload size limit (Railway proxy)
Railway's default proxy allows up to **100MB** requests.
For larger videos (up to 500MB), add this variable:
```
RAILWAY_DEPLOYMENT_OVERLAP_SECONDS=30
```
And optionally use the **Railway Pro plan** which supports larger payloads.

### 5. Get your URL
After deploy (~2-3 min), Railway gives you a public URL like:
`https://video2gif-production-abc123.up.railway.app`

Share that with anyone — done!

---

## Local development
```bash
# Install ffmpeg (macOS)
brew install ffmpeg

# Install ffmpeg (Ubuntu/Debian)
sudo apt-get install ffmpeg

# Install Python deps
pip install -r requirements.txt

# Run
python app.py
# Visit http://localhost:5000
```

---

## Architecture

```
Browser (HTML/JS)
    │
    ├── POST /api/convert   → saves video, spawns background thread, returns job_id
    │
    ├── GET  /api/status/:id → polls progress (queued → processing → done/error)
    │
    ├── GET  /api/preview/:id → streams GIF inline for preview
    │
    └── GET  /api/download/:id → serves GIF as attachment download
```

### FFmpeg pipeline (two-pass)
```
Pass 1: ffmpeg -i input.mp4 -vf "fps=N,scale=W:H,palettegen=stats_mode=diff:max_colors=256" palette.png
Pass 2: ffmpeg -i input.mp4 -i palette.png -lavfi "fps=N,scale=W:H [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" output.gif
```

`stats_mode=diff` analyzes motion between frames for a better palette.  
`diff_mode=rectangle` only redraws changed regions per frame → smaller file.  
`bayer_scale=3` is the sweet spot between smooth gradients and no banding.
