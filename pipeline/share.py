"""Partage d'une vidéo vers le téléphone : lien secret + QR code + petite page mobile.

Le téléphone ouvre la page (Wi-Fi ou lien public), copie la légende et envoie la vidéo vers TikTok
(menu « Partager ») ; sinon il la télécharge pour l'importer dans TikTok.
"""
import html
import json
import secrets
import socket
import threading
import time

TTL = 3 * 3600
_SHARES = {}
_LOCK = threading.Lock()
PUBLIC_BASE = {"url": ""}          # rempli quand le lien public (tunnel HTTPS) est actif


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))        # rien n'est envoyé : sert juste à trouver l'adresse du Wi-Fi
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def create(stem, caption, product):
    now = time.time()
    with _LOCK:
        for t in [t for t, e in _SHARES.items() if e["expires"] < now]:
            _SHARES.pop(t, None)
        token = secrets.token_urlsafe(18)
        _SHARES[token] = dict(stem=stem, caption=caption, product=product, expires=now + TTL)
    return token


def get(token):
    with _LOCK:
        e = _SHARES.get(token)
        if e and e["expires"] >= time.time():
            return dict(e)
    return None


def set_caption(token, caption):
    with _LOCK:
        if token in _SHARES:
            _SHARES[token]["caption"] = caption
            return True
    return False


def base_url(port):
    return PUBLIC_BASE["url"] or f"http://{lan_ip()}:{port}"


def qr_svg(url):
    import segno
    return segno.make(url, error="m").svg_inline(scale=6, border=2, dark="#111111", light="#ffffff")


def page_html(token, e):
    cap = json.dumps(e["caption"], ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(e["product"] or "Ta vidéo")
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>
<style>
:root{{color-scheme:light dark}}
body{{margin:0;font:16px/1.45 -apple-system,system-ui,sans-serif;background:#0e0e12;color:#f3f3f7}}
main{{max-width:520px;margin:0 auto;padding:16px 16px 40px;display:grid;gap:14px}}
h1{{font-size:20px;margin:6px 0 0}}
video{{width:100%;border-radius:14px;background:#000;max-height:60vh}}
button,a.btn{{appearance:none;border:0;border-radius:14px;padding:15px 18px;font:inherit;font-weight:700;text-align:center;text-decoration:none;display:block;width:100%;box-sizing:border-box;cursor:pointer}}
.primary{{background:#fe2c55;color:#fff}} .ghost{{background:#23232c;color:#f3f3f7}}
button[disabled]{{opacity:.55}}
textarea{{width:100%;box-sizing:border-box;min-height:120px;border-radius:12px;border:1px solid #33333f;background:#17171d;color:inherit;font:inherit;padding:12px}}
.msg{{font-size:14px;background:#17171d;border-radius:12px;padding:10px 12px;min-height:20px}}
ol{{margin:0;padding-left:20px;color:#b9b9c6;font-size:14px}}
</style></head><body><main>
<h1>{title}</h1>
<video id="v" src="/p/{token}/video.mp4" poster="/p/{token}/cover.jpg" controls playsinline preload="metadata"></video>
<button class="primary" id="open" disabled>📲 Ouvrir dans TikTok</button>
<div class="msg" id="msg">Chargement de la vidéo…</div>
<a class="btn ghost" id="dl" href="/p/{token}/video.mp4" download="video.mp4">⬇️ Télécharger la vidéo</a>
<textarea id="cap" readonly></textarea>
<button class="ghost" id="copy">📋 Copier la légende</button>
<ol><li>Appuie sur « Ouvrir dans TikTok » et choisis <b>TikTok</b> dans la liste.</li>
<li>Appuie sur Suivant, puis dans la description fais un appui long → <b>Coller</b>.</li>
<li>Ajoute le <b>panier orange</b> (Ajouter un lien → Produits) et publie.</li></ol>
<script>
const CAPTION = {cap};
const $ = s => document.querySelector(s);
$('#cap').value = CAPTION;
function copySync(text) {{
  const ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly','');
  ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, 99999);
  let ok = false; try {{ ok = document.execCommand('copy'); }} catch (e) {{}} document.body.removeChild(ta); return ok;
}}
$('#copy').onclick = () => {{ const ok = copySync(CAPTION); $('#msg').textContent = ok ? 'Légende copiée ✓' : 'Sélectionne le texte et copie-le à la main.'; }};
let file = null;
(async () => {{
  try {{
    const r = await fetch('/p/{token}/video.mp4'); const blob = await r.blob();
    file = new File([blob], 'video.mp4', {{ type: 'video/mp4' }});
    $('#open').disabled = false; $('#msg').textContent = 'Prêt ✓ La légende sera copiée quand tu appuies sur le bouton.';
  }} catch (e) {{ $('#msg').textContent = 'Impossible de charger la vidéo : utilise « Télécharger la vidéo ».'; }}
}})();
$('#open').onclick = async () => {{
  const copied = copySync(CAPTION);                                  // d'abord, de façon immédiate
  try {{
    if (file && navigator.canShare && navigator.canShare({{ files: [file] }})) {{
      await navigator.share({{ files: [file] }});
      $('#msg').textContent = (copied ? 'Légende copiée ✓ ' : '') + 'Colle-la dans la description de TikTok.';
      return;
    }}
  }} catch (e) {{ if (e && e.name === 'AbortError') return; }}
  $('#msg').textContent = (copied ? 'Légende copiée ✓ ' : '') + "Ton téléphone ne peut pas ouvrir TikTok directement depuis cette page : télécharge la vidéo ci-dessous, puis dans TikTok appuie sur + → Importer.";
}};
</script></main></body></html>"""
