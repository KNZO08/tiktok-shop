"""Dashboard local : python server.py  ->  http://localhost:8765"""
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from pipeline import config, jobs, media, publish, voice

PORT = int(os.environ.get("PORT", 8765))
MANAGER = jobs.Manager()
PUBLISH = {}                     # nom de vidéo -> {state: sending|done|error, message}
PUBLISH_LOCK = threading.Lock()


def _publish_worker(stem, caption):
    def status(msg):
        with PUBLISH_LOCK:
            PUBLISH[stem] = dict(state="sending", message=msg)
    try:
        msg = publish.send_draft(config.OUTPUTS / f"{stem}.mp4", caption, config.load_settings(), status)
        with PUBLISH_LOCK:
            PUBLISH[stem] = dict(state="done", message=msg)
    except publish.PublishError as e:
        with PUBLISH_LOCK:
            PUBLISH[stem] = dict(state="error", message=str(e))
    except Exception as e:
        with PUBLISH_LOCK:
            PUBLISH[stem] = dict(state="error", message="Erreur inattendue : " + str(e)[:200])


def ready_state():
    s = config.load_settings()
    return {"ffmpeg": True, "api_key": bool(s["anthropic_api_key"]), "mode": "api" if s["anthropic_api_key"] else "inbox"}


class Handler(BaseHTTPRequestHandler):
    server_version = "TikTokShopGen/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---------- utilitaires
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return {}

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("localhost", "127.0.0.1", "[::1]")

    def _origin_ok(self):
        o = self.headers.get("Origin")
        if not o:
            return True
        h = urlparse(o).hostname
        return h in ("localhost", "127.0.0.1", "::1")

    def _file(self, path, cache=False):
        path = Path(path)
        if not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m[1]:
                    start = int(m[1])
                    end = int(m[2]) if m[2] else size - 1
                elif m[2]:
                    start = max(0, size - int(m[2]))
                end = min(end, size - 1)
                code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    # ---------- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self.send_error(403)
        u = urlparse(self.path)
        p = unquote(u.path)
        if p == "/api/state":
            return self._json(dict(
                jobs=MANAGER.snapshot(),
                history=MANAGER.history(),
                settings=config.public_settings(),
                ready=ready_state(),
                publish=dict(PUBLISH),
            ))
        if p == "/api/voice-sample":
            v = parse_qs(u.query).get("voice", [config.load_settings()["voice"]])[0]
            if v not in [x["id"] for x in config.VOICES]:
                return self.send_error(400)
            try:
                f = voice.sample(v, config.load_settings()["voice_rate"], config.WORK / "samples" / f"{v}.mp3")
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            return self._file(f)
        if p.startswith("/outputs/"):
            f = (config.OUTPUTS / p[len("/outputs/"):]).resolve()
            if f.parent != config.OUTPUTS.resolve():
                return self.send_error(403)
            return self._file(f)
        rel = "index.html" if p in ("/", "") else p.lstrip("/")
        f = (config.WEB / rel).resolve()
        if config.WEB.resolve() not in f.parents and f != config.WEB.resolve():
            return self.send_error(403)
        return self._file(f)

    def do_POST(self):
        if not (self._host_ok() and self._origin_ok()):
            return self.send_error(403)
        p = urlparse(self.path).path
        body = self._body()
        if p == "/api/generate":
            urls = [u.strip() for u in body.get("urls", []) if isinstance(u, str) and u.strip()]
            if not urls:
                return self._json({"error": "Aucun lien."}, 400)
            return self._json({"jobs": MANAGER.submit(urls)})
        m = re.match(r"/api/jobs/([0-9a-f]+)/(cancel|retry)$", p)
        if m:
            if m[2] == "cancel":
                return self._json({"ok": MANAGER.cancel(m[1])})
            return self._json({"jobs": MANAGER.retry(m[1])})
        if p == "/api/publish":
            stem = Path(body.get("name", "")).name.rsplit(".", 1)[0]
            if not (config.OUTPUTS / f"{stem}.mp4").exists():
                return self._json({"error": "Vidéo introuvable."}, 404)
            s = config.load_settings()
            if not (s.get("uploadpost_key") and s.get("uploadpost_user")):
                return self._json({"error": "not_configured"}, 400)
            with PUBLISH_LOCK:
                if PUBLISH.get(stem, {}).get("state") == "sending":
                    return self._json({"ok": True})
                PUBLISH[stem] = dict(state="sending", message="Envoi en cours…")
            threading.Thread(target=_publish_worker, args=(stem, str(body.get("caption", ""))[:2200]), daemon=True).start()
            return self._json({"ok": True})
        if p == "/api/test-uploadpost":
            key = config.load_settings().get("uploadpost_key", "")
            if not key:
                return self._json({"ok": False, "message": "Aucune clé enregistrée."})
            ok, msg = publish.check_key(key)
            return self._json({"ok": ok, "message": msg})
        if p == "/api/rerender":
            try:
                return self._json({"jobs": MANAGER.rerender(Path(body.get("name", "")).name)})
            except Exception:
                return self._json({"error": "Vidéo introuvable."}, 404)
        if p == "/api/jobs/clear":
            MANAGER.clear_finished()
            return self._json({"ok": True})
        if p == "/api/settings":
            patch = {}
            for k in ("model", "voice", "voice_rate", "mirror", "concurrency", "extra_instructions", "layout", "uploadpost_user", "hashtags"):
                if k in body:
                    patch[k] = body[k]
            if isinstance(body.get("anthropic_api_key"), str) and body["anthropic_api_key"].strip():
                patch["anthropic_api_key"] = body["anthropic_api_key"].strip()
            if isinstance(body.get("uploadpost_key"), str) and body["uploadpost_key"].strip():
                patch["uploadpost_key"] = body["uploadpost_key"].strip()
            if body.get("clear_api_key"):
                patch["anthropic_api_key"] = ""
            if "layout" in patch:
                lo = patch["layout"]
                patch["layout"] = {
                    "height_pct": min(60, max(15, float(lo.get("height_pct", 34)))),
                    "right_pct": min(40, max(0, float(lo.get("right_pct", 15)))),
                    "bottom_pct": min(50, max(0, float(lo.get("bottom_pct", 19)))),
                }
            if "concurrency" in patch:
                patch["concurrency"] = min(6, max(1, int(patch["concurrency"])))
            config.save_settings(patch)
            return self._json(config.public_settings())
        if p == "/api/test-key":
            return self._json(test_key())
        if p == "/api/reveal":
            name = Path(body.get("name", "")).name
            f = config.OUTPUTS / name
            if f.exists() and sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(f)])
            return self._json({"ok": True})
        return self.send_error(404)

    def do_DELETE(self):
        if not (self._host_ok() and self._origin_ok()):
            return self.send_error(403)
        m = re.match(r"/api/history/([\w.\-]+)$", urlparse(self.path).path)
        if m:
            MANAGER.delete_history(m[1])
            return self._json({"ok": True})
        return self.send_error(404)


def test_key():
    s = config.load_settings()
    if not s["anthropic_api_key"]:
        return {"ok": False, "message": "Aucune clé enregistrée."}
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=s["anthropic_api_key"], max_retries=1, timeout=20)
        c.models.retrieve(s["model"])
        return {"ok": True, "message": "Clé valide ✓"}
    except Exception as e:
        name = e.__class__.__name__
        if "Authentication" in name:
            return {"ok": False, "message": "Clé refusée par Anthropic."}
        if "NotFound" in name:
            return {"ok": False, "message": "Clé valide, mais ce modèle n'existe pas pour ton compte."}
        return {"ok": False, "message": f"Impossible de vérifier ({name})."}


def main():
    port = PORT
    for _ in range(20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("Aucun port libre.")
    url = f"http://localhost:{port}"
    print(f"\n  Générateur TikTok Shop prêt : {url}\n  (laisse cette fenêtre ouverte ; Ctrl+C pour arrêter)\n")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
