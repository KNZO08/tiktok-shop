"""Mode « Claude dans la conversation » : sans clé API, le dashboard dépose une demande de script
(captures + légende + durée) et attend la réponse de Claude.

    python -m pipeline.inbox            -> liste les demandes en attente
"""
import json
import sys
import time
from pathlib import Path

from . import config

INBOX = config.WORK / "inbox"
MAX_WAIT = 3 * 3600


def ask(job_id, kind, payload, on_wait=None, should_cancel=None):
    """Dépose une demande et attend `reply.json` ; renvoie le dict de réponse."""
    d = INBOX / job_id
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.glob("request_*.json"))) + 1
    req = d / f"request_{n}.json"
    reply = d / f"reply_{n}.json"
    reply.unlink(missing_ok=True)
    payload = dict(payload, kind=kind, job_id=job_id, reply_file=str(reply), created=time.time())
    req.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    if on_wait:
        on_wait(True)
    t0 = time.time()
    try:
        while True:
            if should_cancel and should_cancel():
                from .media import Cancelled
                raise Cancelled()
            if reply.exists():
                time.sleep(0.4)          # laisse finir l'écriture
                try:
                    return json.loads(reply.read_text())
                except json.JSONDecodeError:
                    raise RuntimeError("La réponse de Claude n'est pas un JSON valide.")
            if time.time() - t0 > MAX_WAIT:
                raise RuntimeError("Personne n'a écrit le script à temps (3 h). Relance la vidéo.")
            time.sleep(1.0)
    finally:
        if on_wait:
            on_wait(False)


def pending():
    out = []
    for req in sorted(INBOX.glob("*/request_*.json")):
        reply = req.with_name(req.name.replace("request_", "reply_"))
        if reply.exists():
            continue
        try:
            d = json.loads(req.read_text())
        except Exception:
            continue
        if time.time() - d.get("created", 0) > MAX_WAIT:
            continue
        out.append(d)
    return out


def main():
    items = pending()
    if not items:
        print("Aucune demande en attente.")
        return
    for d in items:
        print("=" * 70)
        print(f"JOB {d['job_id']}  ({d['kind']})  durée vidéo {d['duration']:.1f}s" + (f" -> vise ~{d['target_words']} mots" if "target_words" in d else ""))
        print("Légende :", (d.get("original_caption") or d.get("caption") or "(aucune)")[:400], "  <- à traduire en français dans \"caption\"")
        if d.get("extra_instructions"):
            print("Consignes du vendeur :", d["extra_instructions"])
        print("Planche de captures :", d.get("sheet"))
        if d["kind"] == "dub":
            print(f"DOUBLAGE : langue d'origine = {d['language']} ; traduis chaque phrase en français (même id, max_words à respecter) :")
            for sl in d["segments"]:
                print(f"  [{sl['id']}] {sl['start']:.1f}-{sl['end']:.1f}s (max {sl['max_words']} mots) : {sl['text']}")
        if d["kind"] == "adjust":
            print(f"AJUSTEMENT : la voix dure {d['measured']:.1f}s, il faut {d['duration']:.1f}s ; script actuel ({d['words_now']} mots -> ~{d['target_words']}) :")
            print(json.dumps(d["previous"], ensure_ascii=False))
        print("Réponse à écrire dans :", d["reply_file"])
    print("=" * 70)
    print('Format doublage : {"product": "...", "caption": "...", "segments": [{"id": 0, "text": "traduction FR", "gesture": "talk|wow|open|point|raise|count|wave|shrug|emphasis"}]}   (pas de geste basket)')
    print('Format : {"product": "...", "caption": "légende TikTok traduite en français + hashtags", "segments": [{"text": "...", "gesture": "wow|open|point|raise|count|wave|shrug|emphasis|talk|basket"}]}')


if __name__ == "__main__":
    main()
