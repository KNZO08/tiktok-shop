"""Écoute de la vidéo d'origine : reconnaissance vocale locale et gratuite (faster-whisper).

Tourne dans un processus séparé (voir stt_cli.py) : plus stable et la mémoire est rendue ensuite.
La première fois, le modèle (~500 Mo pour « small ») est téléchargé une seule fois.
"""
import json
import subprocess
import sys
from pathlib import Path

from . import media

ROOT = Path(__file__).resolve().parent.parent


def extract_audio(video, out):
    media.run(["-i", video, "-vn", "-ac", 1, "-ar", 16000, "-c:a", "pcm_s16le", out])
    return out


def transcribe(video, workdir, model="small"):
    """-> dict(language, probability, segments=[{start,end,text}], words, speech_seconds)."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    wav = extract_audio(video, workdir / "audio16k.wav")
    out = workdir / "transcript.json"
    out.unlink(missing_ok=True)
    p = subprocess.run([sys.executable, "-m", "pipeline.stt_cli", str(wav), model, str(out)], cwd=ROOT,
                       capture_output=True, text=True, timeout=1800)
    if p.returncode != 0 or not out.exists():
        tail = [l for l in (p.stderr or "").strip().splitlines() if l.strip() and "objc[" not in l][-1:]
        raise RuntimeError("reconnaissance vocale en échec : " + (tail[0] if tail else "raison inconnue"))
    t = json.loads(out.read_text())
    segs = t["segments"]
    t["words"] = sum(len(x["text"].split()) for x in segs)
    t["speech_seconds"] = round(sum(x["end"] - x["start"] for x in segs), 2)
    return t


def is_speech(t, min_words=6, min_seconds=2.0):
    """Vrai si la vidéo contient vraiment quelqu'un qui parle (et pas déjà en français)."""
    return bool(t) and t["words"] >= min_words and t["speech_seconds"] >= min_seconds and t["language"] != "fr"
