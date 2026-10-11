"""Montage final : miroir + vitesse ajustée + personnage animé à droite + voix off."""
import json
import subprocess
import time
from pathlib import Path

from . import media, voice as voicemod
from .character import ASSETS, CANVAS_SRC, FPS, Animator, Character

OUT_W, OUT_H = 1080, 1920
FADE_ROWS = 28            # fondu en bas du personnage (la capture d'origine est coupée à la taille)


def rest_bbox():
    try:
        return json.loads((ASSETS / "rig.json").read_text())["rest_bbox"]
    except Exception:
        return [180, 64, 668, 797]


def place(layout, scale):
    """Position (px) du coin haut-gauche de la zone de dessin dans la vidéo."""
    bx0, by0, bx1, by1 = rest_bbox()
    x_right = OUT_W * (1 - layout["right_pct"] / 100.0)
    y_bottom = OUT_H * (1 - layout["bottom_pct"] / 100.0)
    cx0, cy0 = CANVAS_SRC[0], CANVAS_SRC[1]
    return int(round(x_right - (bx1 - cx0) * scale)), int(round(y_bottom - (by1 - cy0) * scale))


def compose(video_path, voice_info, segments, out_path, workdir, settings, seed=1,
            on_progress=None, should_cancel=None):
    workdir = Path(workdir)
    layout = settings["layout"]
    src = media.probe(video_path)
    D = src["duration"]
    fit = voicemod.fit_plan(voice_info["duration"], D)
    a, Tf, k = fit["atempo"], fit["final"], fit["video_factor"]

    # 1) voix accordée à la durée finale + volume homogène
    voice_fit = workdir / "voice_fit.wav"
    media.run(["-i", voice_info["path"], "-af", f"atempo={a:.5f},loudnorm=I=-15:TP=-1.5:LRA=9,apad",
               "-t", f"{Tf:.4f}", "-ar", 44100, "-ac", 1, "-c:a", "pcm_s16le", voice_fit])

    # 2) animation calée sur la voix
    n = int(round(Tf * FPS))
    env = voicemod.envelope(voice_fit, FPS, n)
    segs = [dict(start=s / a, end=e / a, gesture=g["gesture"]) for (s, e), g in zip(voice_info["times"], segments)]
    anim = Animator(Tf, env, segs, FPS, seed)
    bx0, by0, bx1, by1 = rest_bbox()
    scale = (layout["height_pct"] / 100.0 * OUT_H) / (by1 - by0)
    char = Character(scale)
    cw, chh = char.size
    X0, Y0 = place(layout, scale)

    # 3) ffmpeg : fond (miroir, vitesse) + calque personnage reçu en direct + voix
    chain = []
    if settings.get("mirror", True):
        chain.append("hflip")
    chain += [f"setpts=PTS*{k:.6f}", f"fps={FPS}",
              f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase", f"crop={OUT_W}:{OUT_H}", "setsar=1",
              "tpad=stop_mode=clone:stop_duration=2"]
    fc = f"[0:v]{','.join(chain)}[bg];[bg][1:v]overlay={X0}:{Y0}:eof_action=pass:format=auto,format=yuv420p[v]"
    cmd = [media.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(video_path),
           "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{cw}x{chh}", "-framerate", str(FPS), "-i", "pipe:0",
           "-i", str(voice_fit),
           "-filter_complex", fc, "-map", "[v]", "-map", "2:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-t", f"{Tf:.4f}", "-movflags", "+faststart", str(out_path)]
    log = open(workdir / "ffmpeg_compose.log", "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
    try:
        for i in range(n):
            if should_cancel and should_cancel():
                proc.kill()
                raise media.Cancelled()
            pose = anim.pose(i)
            pose["fade_rows"] = FADE_ROWS
            proc.stdin.write(char.render(pose).tobytes())
            if on_progress and i % 3 == 0:
                on_progress((i + 1) / n)
        proc.stdin.close()
        proc.wait()
    except BrokenPipeError:
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        log.close()
    if proc.returncode != 0:
        tail = (workdir / "ffmpeg_compose.log").read_text(errors="ignore")[-600:]
        raise RuntimeError("Le montage a échoué : " + tail)
    if on_progress:
        on_progress(1.0)
    return dict(duration=Tf, atempo=a, video_factor=k, source_duration=D, layout_xy=(X0, Y0), scale=scale)


def thumbnail(video, out):
    subprocess.run([media.FFMPEG, "-y", "-loglevel", "error", "-ss", "1.0", "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=270:-2", "-q:v", "4", str(out)], check=False)
