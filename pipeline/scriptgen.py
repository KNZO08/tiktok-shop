"""Écriture du script de voix off à partir de captures de la vidéo (Claude, vision)."""
import base64
import json
import os
import re
from pathlib import Path

from .config import CTA_TEXT
from .character import GESTURES
from . import inbox, media

GESTURE_HELP = {
    "wave": "salut / interpeller le spectateur (début de vidéo)",
    "wow": "accroche, surprise, « regarde ça »",
    "raise": "attirer l'attention, « écoute », une main levée",
    "open": "présenter le produit, bras ouverts",
    "point": "montrer le produit ou un détail à l'écran",
    "count": "énumérer des avantages (un, deux, trois…)",
    "shrug": "lever une objection (« pas besoin de… », « sans effort »)",
    "emphasis": "insister sur un bénéfice ou l'urgence",
    "talk": "neutre, explication",
    "basket": "UNIQUEMENT la toute dernière phrase (appel à l'achat)",
}
GESTURE_ENUM = [g for g in GESTURE_HELP if g in GESTURES]

SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string"},
        "caption": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "gesture": {"type": "string", "enum": GESTURE_ENUM}},
                "required": ["text", "gesture"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["product", "caption", "segments"],
    "additionalProperties": False,
}


def count_words(text):
    return len(re.findall(r"[\wÀ-ÿ'’]+", text))


SYSTEM = f"""Tu es un copywriter français spécialisé dans les vidéos TikTok Shop qui vendent. Tu écris le texte de la voix off d'une vidéo produit.

Règles du texte :
- Français parlé, tutoiement, énergique et naturel, comme un vrai créateur TikTok. Phrases courtes (6 à 14 mots).
- Écris pour être LU À VOIX HAUTE : pas d'emoji, pas de hashtag, pas de parenthèses, pas de symboles ; écris les nombres et les « € » en toutes lettres.
- Appuie-toi UNIQUEMENT sur ce que tu vois dans les captures et sur la légende : ce que fait le produit, comment on l'utilise, ses avantages visibles. N'invente aucun prix, aucune promo, aucun chiffre, aucune garantie, aucun avis client ni promesse médicale ou de santé.
- Structure : (1) accroche choc dès la première phrase, qui donne envie de rester ; (2) deux à quatre bénéfices concrets, dans l'ordre où on les voit dans la vidéo ; (3) une phrase d'envie ou d'urgence douce (« ça part vite », « tout le monde en parle »…) sans chiffre inventé ; (4) la phrase finale.
- La toute dernière phrase est TOUJOURS exactement : « {CTA_TEXT} » (geste « basket »), et c'est la seule phrase avec le geste « basket ».
- Chaque segment = UNE phrase. À chaque segment, choisis le geste du personnage qui colle au sens :
""" + "\n".join(f"    • {g} : {GESTURE_HELP[g]}" for g in GESTURE_ENUM) + """
- Varie les gestes (pas deux fois le même d'affilée si possible).
- « caption » = la description du post TikTok, EN FRANÇAIS : traduis fidèlement la légende d'origine (garde le ton, les emojis et les hashtags d'origine ; traduis les hashtags qui ont un sens clair, garde les autres tels quels). Pas de prix inventé.
- Réponds uniquement avec le JSON demandé : « product » = nom court du produit tel que tu le comprends."""


def _client(settings):
    import anthropic
    key = settings.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Il manque ta clé API Anthropic : clique sur « Réglages » en haut à droite et colle-la.")
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=180)


def _call(client, model, content, settings, system=SYSTEM, schema=None):
    import anthropic
    kwargs = dict(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema or SCHEMA}},
    )
    if re.match(r"claude-(opus|sonnet|fable)-5", model):
        kwargs["output_config"]["effort"] = "medium"
    try:
        try:
            # relance automatique sur un autre modèle si la requête est déclinée
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        except (anthropic.BadRequestError, TypeError):
            resp = client.messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Ta clé API Anthropic est refusée. Vérifie-la dans « Réglages ».") from e
    except anthropic.PermissionDeniedError as e:
        raise RuntimeError("Ta clé API Anthropic n'a pas l'autorisation d'utiliser ce modèle.") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Trop de demandes envoyées à Anthropic. Réessaie dans une minute (ou baisse le nombre de vidéos en parallèle dans Réglages).") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Pas de connexion à Anthropic (Internet ?).") from e
    except anthropic.APIStatusError as e:
        msg = getattr(e, "message", str(e))
        if "credit balance" in msg.lower():
            raise RuntimeError("Ton compte Anthropic n'a plus de crédit : ajoute des crédits sur console.anthropic.com.") from e
        raise RuntimeError(f"Erreur Anthropic ({e.status_code}) : {msg[:300]}") from e
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude a refusé d'écrire ce script. Essaie une autre vidéo.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError("Réponse de Claude illisible, réessaie.") from e


def _image_blocks(frames):
    blocks = []
    for t, path in frames:
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        blocks.append({"type": "text", "text": f"Capture à {t:.1f} s :"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    return blocks


def _target_words(duration, wps):
    return max(8, round(duration * wps))


def use_api(settings):
    return bool(settings.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))


def generate(frames, meta, duration, settings, ctx=None):
    """Premier jet du script (API Anthropic si une clé existe, sinon Claude dans la conversation)."""
    wps = float(settings.get("wps", 3.7))
    words = _target_words(duration, wps)
    if os.environ.get("TSG_DEMO"):
        return normalize(demo_script(meta, words))
    caption = (meta.get("description") or meta.get("title") or "")[:400]
    extra = (settings.get("extra_instructions") or "")[:600]
    if not use_api(settings):
        ctx = ctx or {}
        sheet = Path(frames[0][1]).parent / "planche.jpg"
        media.contact_sheet(frames, sheet)
        reply = inbox.ask(ctx.get("job_id", "manual"), "write", dict(
            duration=duration, target_words=words, wps=wps, caption=caption, original_caption=(meta.get("description") or meta.get("title") or ""), extra_instructions=extra,
            sheet=str(sheet), frames=[dict(t=t, path=str(p)) for t, p in frames], rules=SYSTEM,
        ), ctx.get("on_wait"), ctx.get("should_cancel"))
        return normalize(reply)
    client = _client(settings)
    intro = (
        f"Voici {len(frames)} captures réparties sur une vidéo produit TikTok Shop de {duration:.0f} secondes.\n"
        f"Légende d'origine : « {caption or '(aucune)'} »\n\n"
        f"Écris la voix off. Elle doit durer {duration:.0f} secondes : une voix dynamique dit environ {wps:.1f} mots par seconde, "
        f"donc vise {words} mots au total (entre {round(words * 0.93)} et {round(words * 1.05)}), phrase finale comprise."
    )
    if extra:
        intro += "\n\nConsignes supplémentaires du vendeur : " + extra
    content = [{"type": "text", "text": intro}] + _image_blocks(frames)
    return normalize(_call(client, settings["model"], content, settings))


def adjust(script, measured, target, settings, ctx=None):
    """Réécrit le script pour que la voix dure `target` secondes (elle dure `measured`)."""
    words_now = sum(count_words(s["text"]) for s in script["segments"])
    words_new = max(8, round(words_now * target / measured))
    if os.environ.get("TSG_DEMO"):
        return normalize(demo_script({"title": script.get("product", "")}, words_new))
    if not use_api(settings):
        ctx = ctx or {}
        reply = inbox.ask(ctx.get("job_id", "manual"), "adjust", dict(
            duration=target, measured=measured, words_now=words_now, target_words=words_new, previous=script,
            caption="", sheet=ctx.get("sheet", ""), rules=SYSTEM,
        ), ctx.get("on_wait"), ctx.get("should_cancel"))
        return normalize(reply)
    client = _client(settings)
    verb = "raccourcis" if measured > target else "allonge"
    content = [{"type": "text", "text": (
        f"Voici le script actuel (JSON) :\n{json.dumps(script, ensure_ascii=False)}\n\n"
        f"Lu à voix haute, il dure {measured:.1f} s alors qu'il doit durer {target:.1f} s. "
        f"Il fait {words_now} mots : {verb}-le pour arriver à environ {words_new} mots. "
        "Garde le même produit, le même ton, la structure et la phrase finale identique."
    )}]
    return normalize(_call(client, settings["model"], content, settings))


# ------------------------------------------------------------------------------------
def _split_sentences(text):
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def normalize(script):
    """Nettoie : une phrase par segment, phrase finale exacte, gestes valides."""
    segs = []
    for s in script.get("segments", []):
        g = s.get("gesture") if s.get("gesture") in GESTURES else "talk"
        for sent in _split_sentences(s.get("text", "")):
            sent = re.sub(r"\s+", " ", sent.replace("«", "").replace("»", "")).strip()
            if sent:
                segs.append({"text": sent, "gesture": g})
    # la phrase finale
    segs = [s for s in segs if "panier orange" not in s["text"].lower()]
    for s in segs:
        if s["gesture"] == "basket":
            s["gesture"] = "emphasis"
    segs.append({"text": CTA_TEXT, "gesture": "basket"})
    # fusionne les segments trop courts
    merged = []
    for s in segs:
        if merged and count_words(s["text"]) < 3 and s["gesture"] != "basket":
            merged[-1]["text"] += " " + s["text"]
        else:
            merged.append(s)
    return {"product": (script.get("product") or "").strip(), "caption": (script.get("caption") or "").strip(), "segments": merged}


def demo_script(meta, words):
    """Script générique (mode test sans clé API)."""
    base = [
        ("Regarde bien ce que je viens de trouver, c'est une vraie pépite !", "wow"),
        ("Franchement, c'est le genre de produit dont on se demande comment on faisait sans.", "open"),
        ("Il est pratique, solide, et tu le prends en main en deux secondes.", "count"),
        ("Tu vois le résultat à l'écran, c'est propre et ça change tout au quotidien.", "point"),
        ("Pas besoin d'être un expert, tout le monde peut s'en servir tout de suite.", "shrug"),
        ("Et je te le dis, ça part vite, tout le monde en parle en ce moment.", "emphasis"),
    ]
    out, n = [], 0
    for t, g in base:
        if n >= words - 13:
            break
        out.append({"text": t, "gesture": g})
        n += count_words(t)
    return {"product": (meta.get("title") or "Produit")[:40], "caption": ((meta.get("description") or meta.get("title") or "Produit") + " 🔥")[:200], "segments": out}


# ------------------------------------------------------------------------------------
#  Doublage : traduire en français ce qui est dit dans la vidéo
# ------------------------------------------------------------------------------------
DUB_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string"},
        "caption": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"},
                               "gesture": {"type": "string", "enum": GESTURE_ENUM}},
                "required": ["id", "text", "gesture"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["product", "caption", "segments"],
    "additionalProperties": False,
}

DUB_SYSTEM = """Tu es traducteur-doubleur pour des vidéos TikTok Shop. On te donne ce que disent les personnes dans la vidéo (reconnaissance vocale, avec l'instant de chaque phrase) ; tu écris la version française qui sera dite à voix haute à leur place.

Règles :
- Français parlé, naturel, énergique, comme un vrai créateur TikTok ; tutoiement si la personne s'adresse au public.
- Fidèle au sens et au ton : n'invente rien (ni prix, ni promo, ni garantie, ni chiffre, ni promesse de santé), ne retire pas d'information importante. Corrige les erreurs évidentes de la reconnaissance vocale grâce aux captures et à la légende.
- Une phrase traduite par segment, avec le même « id ». Chaque phrase doit pouvoir être DITE dans le temps du segment : respecte « max_words » (raccourcis en gardant l'essentiel si le français est plus long).
- Écris pour être lu à voix haute : pas d'emoji, pas de hashtag, pas de parenthèses ; nombres et « € » en toutes lettres.
- À chaque segment, choisis le geste du personnage qui colle au sens :
""" + "\n".join(f"    • {g} : {GESTURE_HELP[g]}" for g in GESTURE_ENUM if g != "basket") + """
- N'utilise jamais le geste « basket » (réservé à l'appel à l'achat final, ajouté ailleurs). Varie les gestes.
- « caption » = la description du post TikTok, EN FRANÇAIS : traduis la légende d'origine (garde le ton, les emojis et les hashtags d'origine ; traduis les hashtags qui ont un sens clair, garde les autres tels quels).
- « product » = nom court du produit tel que tu le comprends."""


def dub_slots(transcript, wps):
    """Segments de la reconnaissance vocale -> places de doublage (avec un nombre de mots maximum)."""
    out = []
    for i, sg in enumerate(transcript["segments"]):
        dur = max(0.6, sg["end"] - sg["start"])
        out.append(dict(id=i, start=sg["start"], end=sg["end"], text=sg["text"], max_words=max(2, round(dur * wps * 0.95))))
    return out


def translate_dub(transcript, frames, meta, duration, settings, ctx=None):
    """-> {product, caption, segments:[{text, gesture, start, end}]} ; chaque segment garde son instant d'origine."""
    wps = float(settings.get("wps", 3.7))
    slots = dub_slots(transcript, wps)
    caption = (meta.get("description") or meta.get("title") or "")[:600]
    if os.environ.get("TSG_DEMO"):
        reply = {"product": (meta.get("title") or "Produit")[:40], "caption": caption,
                 "segments": [dict(id=sl["id"], text=("Traduction de test : " + sl["text"])[:80], gesture="talk") for sl in slots]}
    elif not use_api(settings):
        ctx = ctx or {}
        sheet = Path(frames[0][1]).parent / "planche.jpg"
        media.contact_sheet(frames, sheet)
        reply = inbox.ask(ctx.get("job_id", "manual"), "dub", dict(
            duration=duration, language=transcript["language"], segments=slots, wps=wps,
            original_caption=caption, caption=caption, sheet=str(sheet), rules=DUB_SYSTEM,
            extra_instructions=(settings.get("extra_instructions") or "")[:600],
        ), ctx.get("on_wait"), ctx.get("should_cancel"))
    else:
        client = _client(settings)
        intro = (f"Vidéo produit TikTok Shop de {duration:.0f} secondes. Langue parlée détectée : {transcript['language']}.\n"
                 f"Légende d'origine : « {caption or '(aucune)'} »\n\nVoici les phrases dites (JSON) :\n"
                 + json.dumps(slots, ensure_ascii=False) + "\n\nÉcris la version française, une phrase par segment.")
        if settings.get("extra_instructions"):
            intro += "\n\nConsignes supplémentaires du vendeur : " + settings["extra_instructions"][:600]
        content = [{"type": "text", "text": intro}] + _image_blocks(frames)
        reply = _call(client, settings["model"], content, settings, system=DUB_SYSTEM, schema=DUB_SCHEMA)
    return normalize_dub(reply, slots)


def normalize_dub(reply, slots):
    by_id = {}
    for sg in reply.get("segments", []):
        try:
            by_id[int(sg["id"])] = sg
        except (KeyError, TypeError, ValueError):
            continue
    segs = []
    for sl in slots:
        sg = by_id.get(sl["id"])
        text = re.sub(r"\s+", " ", (sg or {}).get("text", "")).strip()
        if not text:
            raise RuntimeError(f"La traduction est incomplète (phrase {sl['id'] + 1} manquante).")
        g = (sg or {}).get("gesture")
        segs.append(dict(text=text, gesture=g if g in GESTURES and g != "basket" else "talk", start=sl["start"], end=sl["end"]))
    return {"product": (reply.get("product") or "").strip(), "caption": (reply.get("caption") or "").strip(),
            "mode": "dub", "segments": segs}
