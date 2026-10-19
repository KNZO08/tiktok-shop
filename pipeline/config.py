"""Chemins et réglages (stockés dans settings.json, jamais envoyés ailleurs)."""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"
WORK = ROOT / "work"
WEB = ROOT / "web"
SETTINGS_FILE = ROOT / "settings.json"

CTA_TEXT = "Si tu veux commander, je te laisse cliquer sur le panier orange."

DEFAULTS = {
    "anthropic_api_key": "",
    "model": "claude-opus-5-5",
    "voice": "fr-FR-RemyMultilingualNeural",
    "voice_rate": "+6%",
    "mirror": True,
    "concurrency": 2,
    "extra_instructions": "",
    "layout": {"height_pct": 34, "right_pct": 15, "bottom_pct": 19},
    "dub_mode": "auto",        # auto : si la vidéo contient quelqu'un qui parle, on traduit et on double en français ; off : toujours un script vendeur
    "dub_cta": True,           # ajoute « Si tu veux commander… » à la fin des vidéos doublées
    "stt_model": "small",      # modèle de reconnaissance vocale (small = bon compromis)
    "hashtags": "#tiktokmademebuyit #tiktokshop #fyp #bonplan",
    "wps": 3.7,           # mots par seconde (mesuré et affiné automatiquement)
}

VOICES = [
    {"id": "fr-FR-RemyMultilingualNeural", "label": "Rémy — homme, naturel (recommandé)"},
    {"id": "fr-FR-HenriNeural", "label": "Henri — homme, posé"},
    {"id": "fr-FR-VivienneMultilingualNeural", "label": "Vivienne — femme, chaleureuse"},
    {"id": "fr-FR-DeniseNeural", "label": "Denise — femme, claire"},
    {"id": "fr-FR-EloiseNeural", "label": "Éloïse — femme, jeune"},
]

_lock = threading.Lock()


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings():
    with _lock:
        data = {}
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                data = {}
        s = _merge(DEFAULTS, data)
    if not s["anthropic_api_key"]:
        s["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    return s


def save_settings(patch):
    with _lock:
        cur = {}
        if SETTINGS_FILE.exists():
            try:
                cur = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                cur = {}
        cur = _merge(cur, patch)
        SETTINGS_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False))
        try:
            os.chmod(SETTINGS_FILE, 0o600)
        except OSError:
            pass
    return load_settings()


def public_settings():
    s = load_settings()
    key = s["anthropic_api_key"]
    s = dict(s)
    s["anthropic_api_key"] = ""
    s["has_api_key"] = bool(key)
    s["api_key_hint"] = (key[:7] + "…" + key[-4:]) if len(key) > 14 else ""
    s["voices"] = VOICES
    return s
