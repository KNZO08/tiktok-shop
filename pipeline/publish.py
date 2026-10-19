"""Envoi d'une vidéo dans les BROUILLONS TikTok via Upload-Post (service déjà validé par TikTok).

La vidéo arrive dans l'appli TikTok (notification / boîte de réception) : il reste à ajouter le panier orange
(produit TikTok Shop), coller la légende et publier. Doc : https://docs.upload-post.com/api/upload-video
"""
import os
import time
from pathlib import Path

import requests

BASE = os.environ.get("UPLOAD_POST_BASE", "https://api.upload-post.com/api").rstrip("/")


class PublishError(Exception):
    pass


def _headers(key):
    return {"Authorization": f"Apikey {key}"}


def _msg(r):
    try:
        d = r.json()
        return str(d.get("message") or d.get("error") or d)[:300]
    except Exception:
        return (r.text or "")[:300]


def _explain(r):
    m = _msg(r)
    if r.status_code == 401:
        return "Clé Upload-Post refusée : vérifie-la dans Réglages."
    if r.status_code == 403:
        return "Upload-Post refuse TikTok avec ton offre actuelle (TikTok demande une offre payante) : " + m
    if r.status_code == 429:
        return "Limite atteinte (6 envois par minute, 15 par jour et par compte TikTok). Réessaie plus tard. " + m
    return f"Upload-Post a répondu {r.status_code} : {m}"


def check_key(key):
    """Vérifie la clé sans rien envoyer : 404 = clé valide (demande inconnue), 401 = clé refusée."""
    try:
        r = requests.get(f"{BASE}/uploadposts/status", params={"request_id": "ping"}, headers=_headers(key), timeout=20)
    except requests.RequestException:
        return False, "Impossible de joindre Upload-Post (Internet ?)."
    if r.status_code == 401:
        return False, "Clé refusée par Upload-Post."
    if r.status_code in (200, 404, 400):
        return True, "Clé valide ✓"
    return False, f"Réponse inattendue ({r.status_code})."


def send_draft(video_path, caption, settings, on_status=None):
    """Envoie la vidéo dans les brouillons TikTok. Renvoie un message de succès, ou lève PublishError."""
    key, user = settings.get("uploadpost_key", ""), settings.get("uploadpost_user", "")
    if not key or not user:
        raise PublishError("Upload-Post n'est pas configuré : ajoute ta clé et le nom de ton profil dans Réglages.")
    video_path = Path(video_path)
    data = {
        "user": user,
        "platform[]": "tiktok",
        "title": caption,
        "tiktok_title": caption[:2200],
        "post_mode": "MEDIA_UPLOAD",       # brouillon : TikTok ignore la légende, on la colle dans l'appli
        "async_upload": "true",
    }
    if on_status:
        on_status("Envoi de la vidéo à Upload-Post…")
    try:
        with open(video_path, "rb") as f:
            r = requests.post(f"{BASE}/upload", headers=_headers(key), data=data,
                              files={"video": (video_path.name, f, "video/mp4")}, timeout=600)
    except requests.RequestException as e:
        raise PublishError("Envoi impossible (Internet ?) : " + str(e)[:200])
    if r.status_code >= 400:
        raise PublishError(_explain(r))
    body = r.json() if r.content else {}
    req_id = body.get("request_id")
    if not req_id:                                        # réponse directe (envoi rapide)
        res = (body.get("results") or {}).get("tiktok") or {}
        if res.get("success") is False:
            raise PublishError("TikTok a refusé la vidéo : " + str(res.get("error") or res.get("message") or "")[:250])
        return "Vidéo envoyée dans tes brouillons TikTok."
    # envoi en arrière-plan : on suit son avancement
    if on_status:
        on_status("TikTok traite la vidéo…")
    t0 = time.time()
    while time.time() - t0 < 240:
        time.sleep(5)
        try:
            s = requests.get(f"{BASE}/uploadposts/status", params={"request_id": req_id}, headers=_headers(key), timeout=30)
        except requests.RequestException:
            continue
        if s.status_code >= 400:
            if s.status_code == 404:
                continue
            raise PublishError(_explain(s))
        d = s.json()
        st = d.get("status")
        if st == "completed":
            tk = next((x for x in d.get("results", []) if x.get("platform") == "tiktok"), {})
            if tk.get("success") is False:
                raise PublishError("TikTok a refusé la vidéo : " + str(tk.get("message", ""))[:250])
            return "Vidéo envoyée dans tes brouillons TikTok."
        if st == "failed":
            tk = next((x for x in d.get("results", []) if x.get("platform") == "tiktok"), {})
            raise PublishError("L'envoi a échoué : " + str(tk.get("message") or d.get("message") or "raison inconnue")[:250])
    raise PublishError("TikTok met trop de temps à répondre. Regarde dans l'appli TikTok si la vidéo est arrivée.")
