"""Lien public sécurisé (HTTPS) temporaire vers les pages de partage, via Cloudflare « quick tunnel » (gratuit, sans compte).

Nécessaire pour que le téléphone propose TikTok dans le menu « Partager » (le partage de fichiers exige HTTPS).
Seules les adresses /p/<jeton secret> sont servies par le dashboard ; tout le reste y reste refusé (voir server.py).
L'outil `cloudflared` est téléchargé une seule fois dans le dossier .tools/ du projet.
"""
import atexit
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import requests

from . import share

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / ".tools"
STATE = {"state": "idle", "url": "", "message": ""}        # idle | installing | starting | up | error
_proc = {"p": None}
_lock = threading.Lock()


def _asset():
    sysname, mach = sys.platform, platform.machine().lower()
    arm = mach in ("arm64", "aarch64")
    if sysname == "darwin":
        return "cloudflared-darwin-arm64.tgz" if arm else "cloudflared-darwin-amd64.tgz"
    if sysname.startswith("win"):
        return "cloudflared-windows-amd64.exe"
    return "cloudflared-linux-arm64" if arm else "cloudflared-linux-amd64"


def binary_path():
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    return TOOLS / ("cloudflared.exe" if sys.platform.startswith("win") else "cloudflared")


def ensure_binary():
    exe = binary_path()
    if exe.exists():
        return exe
    STATE.update(state="installing", message="Téléchargement de l'outil Cloudflare (une seule fois, ~40 Mo)…")
    asset = _asset()
    TOOLS.mkdir(exist_ok=True)
    rel = requests.get("https://api.github.com/repos/cloudflare/cloudflared/releases/latest", timeout=30)
    rel.raise_for_status()
    info = next((a for a in rel.json().get("assets", []) if a["name"] == asset), None)
    if not info:
        raise RuntimeError("Outil Cloudflare introuvable pour ce système.")
    tmp = TOOLS / (asset + ".part")
    h = hashlib.sha256()
    with requests.get(info["browser_download_url"], stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                h.update(chunk)
    digest = (info.get("digest") or "").replace("sha256:", "")
    if digest and digest != h.hexdigest():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Le fichier téléchargé est corrompu (empreinte différente) : téléchargement annulé.")
    if asset.endswith(".tgz"):
        with tarfile.open(tmp) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("cloudflared") and m.isfile())
            member.name = exe.name
            t.extract(member, TOOLS)
        tmp.unlink(missing_ok=True)
    else:
        tmp.replace(exe)
    exe.chmod(0o755)
    return exe


def _run(port):
    try:
        exe = ensure_binary()
        STATE.update(state="starting", message="Création du lien sécurisé…")
        p = subprocess.Popen([str(exe), "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        _proc["p"] = p
        t0 = time.time()
        url = ""
        for line in p.stderr:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                break
            if time.time() - t0 > 40:
                break
        if not url:
            p.kill()
            raise RuntimeError("Le lien sécurisé n'a pas pu être créé (Internet ?).")
        share.PUBLIC_BASE["url"] = url
        STATE.update(state="up", url=url, message="Lien sécurisé actif.")
        for _ in p.stderr:                      # on garde le tuyau ouvert tant que le tunnel tourne
            pass
        share.PUBLIC_BASE["url"] = ""
        STATE.update(state="idle", url="", message="Le lien sécurisé s'est arrêté.")
    except Exception as e:
        STATE.update(state="error", url="", message=str(e)[:250])
        share.PUBLIC_BASE["url"] = ""


def start(port):
    with _lock:
        if STATE["state"] in ("installing", "starting", "up"):
            return dict(STATE)
        STATE.update(state="installing" if not binary_path().exists() else "starting", url="", message="Démarrage…")
        threading.Thread(target=_run, args=(port,), daemon=True).start()
    return dict(STATE)


def stop():
    p = _proc.get("p")
    if p and p.poll() is None:
        p.terminate()
    share.PUBLIC_BASE["url"] = ""
    STATE.update(state="idle", url="", message="")


atexit.register(stop)
