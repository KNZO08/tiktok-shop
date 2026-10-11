"""Outils ffmpeg : localisation, analyse, extraction d'images, exécution avec progression."""
import re
import shutil
import subprocess
from pathlib import Path


class Cancelled(Exception):
    pass


def ffmpeg_path():
    p = shutil.which("ffmpeg")
    if p:
        return p
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG = ffmpeg_path()


def probe(path):
    """Durée (s), largeur, hauteur, fps, présence d'audio — via la sortie de `ffmpeg -i`."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    txt = r.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", txt)
    if not m:
        raise RuntimeError("Fichier vidéo illisible.")
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    v = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", txt)
    fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", txt)
    return dict(
        duration=dur,
        width=int(v[1]) if v else 0,
        height=int(v[2]) if v else 0,
        fps=float(fps[1]) if fps else 30.0,
        has_audio="Audio:" in txt,
    )


def wav_duration(path):
    import wave
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def extract_frames(video, out_dir, n=8, width=576):
    """n captures réparties sur toute la vidéo -> liste de chemins JPEG."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = probe(video)["duration"]
    paths = []
    for i in range(n):
        t = max(0.0, dur * (i + 0.5) / n)
        p = out_dir / f"frame_{i + 1:02d}.jpg"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-q:v", "3", str(p)], check=False)
        if p.exists():
            paths.append((t, p))
    if not paths:
        raise RuntimeError("Impossible d'extraire des images de la vidéo.")
    return paths


def run(args, duration=None, on_progress=None, should_cancel=None):
    """Lance ffmpeg ; appelle on_progress(0..1) grâce à -progress."""
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"] + [str(a) for a in args]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_lines = []
    try:
        for line in p.stdout:
            if should_cancel and should_cancel():
                p.kill()
                raise Cancelled()
            if duration and on_progress and line.startswith("out_time_ms="):
                try:
                    on_progress(min(1.0, int(line.split("=")[1]) / 1e6 / duration))
                except ValueError:
                    pass
        err = p.stderr.read()
        p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    if p.returncode != 0:
        raise RuntimeError("ffmpeg a échoué : " + (err or "").strip()[-600:])
    return True


def contact_sheet(frames, out, cols=4, tile_w=288):
    """Une seule image avec toutes les captures (avec l'heure) : pratique pour que Claude les voie d'un coup."""
    import cv2
    import numpy as np
    tiles = []
    for t, path in frames:
        im = cv2.imread(str(path))
        if im is None:
            continue
        h = int(im.shape[0] * tile_w / im.shape[1])
        im = cv2.resize(im, (tile_w, h), interpolation=cv2.INTER_AREA)
        cv2.rectangle(im, (0, 0), (74, 22), (0, 0, 0), -1)
        cv2.putText(im, f"{t:4.1f}s", (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(im)
    if not tiles:
        return None
    hh = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, hh - t.shape[0], 0, 0, cv2.BORDER_CONSTANT) for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return Path(out)
