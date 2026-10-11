"""Téléchargement de la vidéo d'origine (TikTok, ou tout lien pris en charge par yt-dlp)."""
import shutil
from pathlib import Path

from .media import FFMPEG, Cancelled


def download(url, out_dir, on_progress=None, should_cancel=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # fichier local (pratique pour tester ou si tu as déjà la vidéo)
    local = Path(url.replace("file://", "")).expanduser()
    if url and local.exists() and local.is_file():
        dest = out_dir / ("source" + local.suffix)
        shutil.copy(local, dest)
        return dest, {"title": local.stem, "description": ""}

    import yt_dlp

    def hook(d):
        if should_cancel and should_cancel():
            raise Cancelled()
        if d.get("status") == "downloading" and on_progress:
            tot = d.get("total_bytes") or d.get("total_bytes_estimate")
            if tot:
                on_progress(min(0.99, d.get("downloaded_bytes", 0) / tot))
        elif d.get("status") == "finished" and on_progress:
            on_progress(1.0)

    opts = {
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "format": "bv*[vcodec~='^(h264|avc)']+ba/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "ffmpeg_location": FFMPEG,
        "retries": 3,
        "socket_timeout": 25,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Cancelled:
        raise
    except Exception as e:  # yt_dlp.utils.DownloadError & co
        msg = str(e).replace("ERROR: ", "").strip()
        hint = ""
        low = msg.lower()
        if "not a valid url" in low:
            hint = " (ce n'est pas un lien : colle l'adresse complète de la vidéo TikTok)"
        elif "private" in low or "unavailable" in low or "not available" in low:
            hint = " (la vidéo est peut-être privée ou supprimée)"
        elif "unsupported url" in low:
            hint = " (ce lien n'est pas reconnu : colle le lien complet de la vidéo TikTok)"
        elif "ip address is blocked" in low or "403" in low or "429" in low:
            hint = " (TikTok refuse l'accès : la vidéo est peut-être supprimée ou privée, ou TikTok bloque temporairement — ouvre le lien dans ton navigateur pour vérifier)"
        raise RuntimeError("Téléchargement impossible : " + msg[-300:] + hint) from e

    files = sorted(out_dir.glob("source.*"))
    files = [f for f in files if f.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".m4v")]
    if not files:
        raise RuntimeError("Téléchargement terminé mais aucun fichier vidéo trouvé.")
    meta = {
        "title": (info or {}).get("title") or "",
        "description": (info or {}).get("description") or "",
        "uploader": (info or {}).get("uploader") or "",
    }
    return files[0], meta
