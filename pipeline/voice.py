"""Voix off française (Microsoft Edge TTS, gratuit) : une phrase à la fois, blancs coupés, collée serrée."""
import asyncio
import wave
from pathlib import Path

import numpy as np

from . import media

SR = 44100
GAP = 0.07          # pause entre deux phrases (s)
SILENCE_CUT = (
    "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.02"
    ":stop_periods=-1:stop_duration=0.14:stop_threshold=-42dB:stop_silence=0.07"
)

# Microsoft a déplacé le service de voix (octobre 2026) : le nouvel hôte marche partout, l'ancien seulement dans certains pays.
# On essaie le nouveau d'abord et on bascule automatiquement sur l'autre en cas de refus (403).
_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
_ENDPOINTS = [
    f"wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key={_TOKEN}",
    f"wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken={_TOKEN}",
]
_state = {"i": 0}


def _use_endpoint(i):
    import edge_tts.communicate as cm
    cm.WSS_URL = _ENDPOINTS[i % len(_ENDPOINTS)]


# Mots que la voix lit mal (lettres épelées, prononciation anglaise) -> écriture phonétique, uniquement pour la voix.
_SPEAK = [
    (r"\bQi\s?2\b", "tchi deux"), (r"\bQi\b", "tchi"), (r"\bUSB[- ]?C\b", "U S B C"), (r"\bUSB\b", "U S B"),
    (r"\bLED\b", "lède"), (r"\bWi-?Fi\b", "ouifi"), (r"\bMagSafe\b", "mag séf"), (r"\bBluetooth\b", "blou tousse"),
    (r"\bHDMI\b", "H D M I"), (r"\bGPS\b", "G P S"), (r"\bAI\b", "A I"), (r"\bTikTok\b", "Tik Tok"),
]


def speak_fix(text):
    import re
    for pat, rep in _SPEAK:
        text = re.sub(pat, rep, text)
    return text


async def _one(text, voice, rate, out, sem):
    import edge_tts
    text = speak_fix(text)
    last = None
    async with sem:
        for attempt in range(6):
            i = _state["i"]
            _use_endpoint(i)
            try:
                await edge_tts.Communicate(text, voice, rate=rate).save(str(out))
                if Path(out).exists() and Path(out).stat().st_size > 800:
                    return
            except Exception as e:  # réseau, refus de Microsoft…
                last = e
                _state["i"] = (i + 1) % len(_ENDPOINTS)     # on essaie l'autre adresse
            await asyncio.sleep(0.8 * (attempt + 1))
    raise RuntimeError("La voix off n'a pas pu être générée (Internet ? ou le service gratuit de Microsoft est en panne) : " + str(last)[:200])


async def _all(texts, voice, rate, outs):
    sem = asyncio.Semaphore(3)
    await asyncio.gather(*[_one(t, voice, rate, o, sem) for t, o in zip(texts, outs)])


def _read_wav(path):
    with wave.open(str(path)) as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def _write_wav(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def build(segments, voice, rate, workdir, should_cancel=None, on_progress=None):
    """-> dict(path, duration, times=[(début, fin)…]) ; tous les segments avec la MÊME voix."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    mp3s = [workdir / f"seg_{i:02d}.mp3" for i in range(len(segments))]
    asyncio.run(_all([s["text"] for s in segments], voice, rate, mp3s))
    chunks, times, t = [], [], 0.0
    gap = np.zeros(int(GAP * SR), np.float32)
    fade = int(0.006 * SR)
    for i, mp3 in enumerate(mp3s):
        if should_cancel and should_cancel():
            raise media.Cancelled()
        wav = workdir / f"seg_{i:02d}.wav"
        media.run(["-i", mp3, "-af", SILENCE_CUT, "-ar", SR, "-ac", 1, "-c:a", "pcm_s16le", wav])
        x = _read_wav(wav)
        if len(x) < SR * 0.15:
            raise RuntimeError("Une phrase de la voix off est vide, réessaie.")
        x[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        x[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        start = t
        chunks.append(x)
        t += len(x) / SR
        times.append((start, t))
        if i < len(mp3s) - 1:
            chunks.append(gap)
            t += GAP
        if on_progress:
            on_progress((i + 1) / len(mp3s))
    out = workdir / "voice_raw.wav"
    _write_wav(out, np.concatenate(chunks))
    return dict(path=out, duration=t, times=times)


def _fit_to_slot(wav_path, slot, max_speed=1.18):
    """Accélère un peu une phrase qui dépasse sa place (jamais plus de max_speed). Renvoie le tableau audio."""
    x = _read_wav(wav_path)
    dur = len(x) / SR
    if slot > 0.4 and dur > slot * 1.03:
        speed = min(dur / slot, max_speed)
        out = Path(wav_path).with_name(Path(wav_path).stem + "_fit.wav")
        media.run(["-i", wav_path, "-af", f"atempo={speed:.4f}", "-ar", SR, "-ac", 1, "-c:a", "pcm_s16le", out])
        x = _read_wav(out)
    return x


def build_anchored(items, video_dur, voice, rate, workdir, should_cancel=None, on_progress=None):
    """Doublage : chaque phrase est placée à l'instant où elle était dite dans la vidéo d'origine.

    items : [{text, start}] ; start=None -> juste après la phrase précédente (appel à l'achat final).
    -> dict(path, duration, times=[(début, fin)…])
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    mp3s = [workdir / f"seg_{i:02d}.mp3" for i in range(len(items))]
    asyncio.run(_all([it["text"] for it in items], voice, rate, mp3s))
    arrays = []
    for i, mp3 in enumerate(mp3s):
        if should_cancel and should_cancel():
            raise media.Cancelled()
        wav = workdir / f"seg_{i:02d}.wav"
        media.run(["-i", mp3, "-af", SILENCE_CUT, "-ar", SR, "-ac", 1, "-c:a", "pcm_s16le", wav])
        # place disponible : jusqu'au début de la phrase suivante (ou la fin de la vidéo)
        nxt = next((it["start"] for it in items[i + 1:] if it.get("start") is not None), None)
        st = items[i].get("start")
        slot = ((nxt if nxt is not None else video_dur) - (st if st is not None else 0)) - 0.08 if st is not None else 0
        x = _fit_to_slot(wav, slot) if st is not None and nxt is not None else _read_wav(wav)
        if len(x) < SR * 0.15:
            raise RuntimeError("Une phrase de la voix off est vide, réessaie.")
        fade = int(0.006 * SR)
        x = x.copy()
        x[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        x[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        arrays.append(x)
        if on_progress:
            on_progress((i + 1) / len(mp3s))
    cursor, times = 0.0, []
    for it, x in zip(items, arrays):
        start = max(it["start"], cursor + GAP) if it.get("start") is not None else cursor + 0.30
        end = start + len(x) / SR
        times.append((start, end))
        cursor = end
    total = np.zeros(int(cursor * SR) + 1, np.float32)
    for (st, _), x in zip(times, arrays):
        i0 = int(st * SR)
        total[i0:i0 + len(x)] += x
    out = workdir / "voice_raw.wav"
    _write_wav(out, total)
    return dict(path=out, duration=cursor, times=times)


def fit_plan_dub(voice_dur, video_dur, a_max=1.12, k_max=1.12):
    """Doublage : la voix suit la vidéo. Trop longue -> on l'accélère un peu, puis on ralentit la vidéo,
    puis la dernière image est figée pour le reste. Trop courte -> on laisse la vidéo telle quelle."""
    if voice_dur <= video_dur:
        return dict(atempo=1.0, final=video_dur, video_factor=1.0)
    a = min(voice_dur / video_dur, a_max)
    v2 = voice_dur / a
    if v2 <= video_dur:
        return dict(atempo=a, final=video_dur, video_factor=1.0)
    k = min(v2 / video_dur, k_max)
    return dict(atempo=a, final=max(video_dur * k, v2), video_factor=k)


def fit_plan(voice_dur, video_dur, a_min=0.93, a_max=1.10):
    """Accorde voix et vidéo : on accélère/ralentit un peu la voix, puis la vidéo pour le reste."""
    ratio = voice_dur / video_dur            # >1 : la voix est trop longue
    a = min(max(ratio, a_min), a_max)        # facteur de vitesse de la voix (atempo)
    final = voice_dur / a
    k = final / video_dur                     # >1 : vidéo ralentie, <1 : accélérée
    return dict(atempo=a, final=final, video_factor=k)


def features(wav_path, fps=30, n_frames=None):
    """Par image : volume de la voix (0..1, ouvre la bouche) et « largeur » de la voyelle (-1 « ou » … +1 « i »)."""
    x = _read_wav(wav_path)
    hop = SR / fps
    n = n_frames or int(np.ceil(len(x) / hop))
    rms = np.zeros(n)
    cent = np.zeros(n)
    win = np.hanning(int(hop)).astype(np.float32)
    freqs = np.fft.rfftfreq(len(win), 1.0 / SR)
    band = (freqs > 200) & (freqs < 5000)
    for i in range(n):
        seg = x[int(i * hop): int((i + 1) * hop)]
        if len(seg) < len(win):
            continue
        rms[i] = np.sqrt(np.mean(seg ** 2))
        if rms[i] > 0.01:
            mag = np.abs(np.fft.rfft(seg[: len(win)] * win))[band]
            f = freqs[band]
            cent[i] = float((mag * f).sum() / (mag.sum() + 1e-9))
    ref = np.percentile(rms[rms > 0.004], 88) if (rms > 0.004).any() else 1.0
    env = np.clip(rms / (ref * 1.05), 0, 1)
    env[rms < 0.006] = 0.0
    wide = np.clip((cent - 1700.0) / 900.0, -1, 1)
    wide[rms < 0.01] = 0.0
    return env, wide


def envelope(wav_path, fps=30, n_frames=None):
    return features(wav_path, fps, n_frames)[0]


def sample(voice, rate, out):
    """Petit extrait pour écouter une voix dans les réglages."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 800:
        return out
    text = "Salut ! Regarde ce produit, il va te changer la vie. Si tu veux commander, je te laisse cliquer sur le panier orange."
    asyncio.run(_all([text], voice, rate, [out]))
    return out
