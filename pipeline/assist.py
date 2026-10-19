"""Publication assistée, gratuite, pour chaque compte TikTok : ouvre TikTok Studio dans le bon profil Chrome
et montre la vidéo dans le Finder (il ne reste qu'à la glisser, coller la légende et ajouter le panier orange)."""
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

STUDIO_URL = "https://www.tiktok.com/tiktokstudio/upload"


def _local_state():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Google/Chrome/Local State"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data/Local State"
    return Path.home() / ".config/google-chrome/Local State"


def chrome_profiles():
    """Profils Chrome de cet ordinateur : [{dir, name}] (aucun e-mail n'est lu ni affiché)."""
    try:
        cache = json.loads(_local_state().read_text()).get("profile", {}).get("info_cache", {})
    except Exception:
        return []
    out = [{"dir": d, "name": (v.get("name") or d)} for d, v in cache.items()]
    out.sort(key=lambda x: (x["dir"] != "Default", x["dir"]))
    return out


def _run(cmd):
    if os.environ.get("TSG_NO_OPEN"):                      # tests : on n'ouvre rien
        print("[assist]", cmd)
        return
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal(path):
    path = str(path)
    if sys.platform == "darwin":
        _run(["open", "-R", path])
    elif sys.platform.startswith("win"):
        _run(["explorer", "/select,", path])
    else:
        _run(["xdg-open", str(Path(path).parent)])


def open_studio(profile=""):
    """Ouvre TikTok Studio ; avec `profile`, dans ce profil Chrome (donc connecté au bon compte TikTok)."""
    profile = profile if re.fullmatch(r"[\w .\-]{1,40}", profile or "") else ""
    if profile:
        if sys.platform == "darwin" and Path("/Applications/Google Chrome.app").exists():
            _run(["open", "-na", "Google Chrome", "--args", f"--profile-directory={profile}", STUDIO_URL])
            return
        chrome = shutil.which("chrome") or shutil.which("google-chrome")
        if sys.platform.startswith("win"):
            _run(["cmd", "/c", "start", "", "chrome", f"--profile-directory={profile}", STUDIO_URL])
            return
        if chrome:
            _run([chrome, f"--profile-directory={profile}", STUDIO_URL])
            return
    if os.environ.get("TSG_NO_OPEN"):
        print("[assist] webbrowser", STUDIO_URL)
    else:
        webbrowser.open(STUDIO_URL)
