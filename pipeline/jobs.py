"""File d'attente : une vidéo = un job, avec étapes et barre de progression."""
import json
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime

from . import compose, config, download, media, scriptgen, stt, voice

# (clé, libellé affiché, début, fin) — part de la barre de progression
STAGES = {
    "queue": ("En attente…", 0.00, 0.00),
    "download": ("Téléchargement de la vidéo", 0.00, 0.09),
    "frames": ("Captures d'écran de la vidéo", 0.09, 0.12),
    "listen": ("Écoute de la vidéo (reconnaissance vocale)", 0.12, 0.18),
    "script": ("Écriture du script vendeur", 0.18, 0.30),
    "voice": ("Génération de la voix off", 0.30, 0.42),
    "fit": ("Ajustement du script à la durée", 0.30, 0.42),
    "render": ("Montage : miroir, personnage animé, voix", 0.42, 0.98),
    "final": ("Finalisation", 0.98, 1.0),
}


class Job:
    def __init__(self, index, url, script=None):
        self.script = script            # script déjà écrit (refaire le montage sans redemander de script)
        self.id = uuid.uuid4().hex[:8]
        self.index = index
        self.url = url.strip()
        self.status = "queued"          # queued | running | done | error | cancelled
        self.stage = "queue"
        self.label = STAGES["queue"][0]
        self.progress = 0.0
        self.error = ""
        self.waiting = False
        self.result = None
        self.created = time.time()
        self.cancel = threading.Event()

    def set_stage(self, key, sub=0.0, label=None):
        _, a, b = STAGES[key]
        self.stage = key
        self.label = label or STAGES[key][0]
        self.progress = max(self.progress, a + (b - a) * min(max(sub, 0.0), 1.0))

    def public(self):
        return dict(id=self.id, index=self.index, url=self.url, status=self.status, stage=self.stage,
                    label=self.label, progress=round(self.progress, 4), error=self.error, waiting=self.waiting,
                    result=self.result)


class Manager:
    def __init__(self):
        self.jobs = {}
        self.order = []
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(max(1, int(config.load_settings()["concurrency"])))
        self._conc = int(config.load_settings()["concurrency"])
        config.OUTPUTS.mkdir(exist_ok=True)

    # -- API
    def submit(self, urls, scripts=None):
        s = config.load_settings()
        if int(s["concurrency"]) != self._conc:
            self._conc = max(1, int(s["concurrency"]))
            self._sem = threading.BoundedSemaphore(self._conc)
        created = []
        with self._lock:
            base = len([j for j in self.jobs.values()])
            for i, u in enumerate(urls):
                if not u.strip():
                    continue
                job = Job(base + i + 1, u, (scripts or {}).get(i))
                self.jobs[job.id] = job
                self.order.append(job.id)
                created.append(job)
        for job in created:
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return [j.public() for j in created]

    def snapshot(self):
        with self._lock:
            return [self.jobs[i].public() for i in self.order]

    def cancel(self, job_id):
        j = self.jobs.get(job_id)
        if j and j.status in ("queued", "running"):
            j.cancel.set()
            return True
        return False

    def retry(self, job_id):
        j = self.jobs.get(job_id)
        if not j:
            return []
        script = None
        if j.result and j.result.get("segments"):          # montage terminé : on le refait avec le même script
            script = dict(product=j.result.get("product", ""), caption=j.result.get("caption", ""), segments=j.result["segments"],
                          mode=j.result.get("mode"), source_language=j.result.get("source_language", ""), original_text=j.result.get("original_text", ""))
        elif j.script:
            script = j.script
        return self.submit([j.url], {0: script} if script else None)

    def rerender(self, name):
        """Refait le montage d'une vidéo de l'historique (même lien, même script, nouvelle animation)."""
        f = config.OUTPUTS / (name.rsplit(".", 1)[0] + ".json")
        d = json.loads(f.read_text())
        return self.submit([d["url"]], {0: dict(product=d.get("product", ""), caption=d.get("caption", ""), segments=d["segments"],
                                                mode=d.get("mode"), source_language=d.get("source_language", ""), original_text=d.get("original_text", ""))})

    def clear_finished(self):
        with self._lock:
            keep = [i for i in self.order if self.jobs[i].status in ("queued", "running")]
            for i in self.order:
                if i not in keep:
                    self.jobs.pop(i, None)
            self.order = keep

    def history(self):
        items = []
        for f in sorted(config.OUTPUTS.glob("*.json"), reverse=True):
            try:
                d = json.loads(f.read_text())
                if (config.OUTPUTS / d["file"]).exists():
                    items.append(d)
            except Exception:
                pass
        return items

    def delete_history(self, name):
        stem = name.rsplit(".", 1)[0]
        for ext in (".mp4", ".json", ".jpg"):
            p = config.OUTPUTS / (stem + ext)
            if p.exists() and p.resolve().parent == config.OUTPUTS.resolve():
                p.unlink()

    # -- exécution
    def _run(self, job):
        with self._sem:
            if job.cancel.is_set():
                job.status = "cancelled"; job.label = "Annulée"
                return
            job.status = "running"
            workdir = config.WORK / "jobs" / job.id
            try:
                process(job, workdir)
                job.status = "done"
                job.progress = 1.0
                job.label = "Terminée"
                shutil.rmtree(workdir, ignore_errors=True)
                shutil.rmtree(config.WORK / "inbox" / job.id, ignore_errors=True)
            except media.Cancelled:
                job.status = "cancelled"; job.label = "Annulée"
            except Exception as e:
                job.status = "error"
                job.error = str(e) or e.__class__.__name__
                job.label = "Échec"
                (workdir.parent / f"{job.id}.error.log").write_text(traceback.format_exc())


def process(job, workdir):
    s = config.load_settings()
    workdir.mkdir(parents=True, exist_ok=True)
    cancel = job.cancel.is_set

    # 1) téléchargement
    job.set_stage("download", 0)
    src, meta = download.download(job.url, workdir / "dl", lambda p: job.set_stage("download", p), cancel)
    info = media.probe(src)
    D = info["duration"]
    if D < 3:
        raise RuntimeError("La vidéo est trop courte (moins de 3 secondes).")
    if D > 180:
        raise RuntimeError("La vidéo dure plus de 3 minutes : ce générateur est prévu pour les vidéos TikTok Shop courtes.")

    # 2) captures d'écran de la vidéo (pour que le script colle à ce qu'on voit)
    job.set_stage("frames", 0)
    n_frames = 6 if D < 15 else 8 if D < 40 else 10
    frames = media.extract_frames(src, workdir / "frames", n_frames)
    job.set_stage("frames", 1)
    if cancel():
        raise media.Cancelled()

    # 2b) y a-t-il quelqu'un qui parle ? alors on traduit et on double en français
    transcript, dub_script = None, None
    if job.script and job.script.get("mode") == "dub":
        dub_script = job.script
    elif not job.script and s.get("dub_mode", "auto") != "off" and info["has_audio"]:
        job.set_stage("listen", 0, "Écoute de la vidéo (la première fois, le modèle de reconnaissance vocale se télécharge)…")
        try:
            transcript = stt.transcribe(src, workdir / "stt", s.get("stt_model", "small"))
        except Exception as e:
            raise RuntimeError("Impossible d'écouter la vidéo : " + str(e)[:200] +
                               " (tu peux choisir « Toujours écrire un script vendeur » dans Réglages.)")
        job.set_stage("listen", 1)
    dub = bool(dub_script) or stt.is_speech(transcript)

    def on_wait(waiting):
        job.waiting = waiting
        if waiting:
            job.set_stage("script", 0.3, "En attente de la traduction de Claude…" if dub else "En attente du script de Claude…")

    ctx = dict(job_id=job.id, on_wait=on_wait, should_cancel=cancel, sheet=str(workdir / "frames" / "planche.jpg"))
    fit = None
    if dub:
        # 3a) doublage : traduction phrase par phrase, voix française posée à l'instant où la phrase était dite
        job.set_stage("script", 0, f"Traduction en français ({(transcript or {}).get('language', '?')} → fr)")
        script = dub_script or scriptgen.translate_dub(transcript, frames, meta, D, s, ctx)
        job.set_stage("script", 1, "Traduction prête")
        items = [dict(text=x["text"], start=x["start"], end=x.get("end"), gesture=x["gesture"])
                 for x in script["segments"] if x.get("start") is not None]
        if s.get("dub_cta", True):
            items.append(dict(text=config.CTA_TEXT, start=None, end=None, gesture="basket"))
        job.set_stage("voice", 0, "Génération de la voix française")
        vinfo = voice.build_anchored(items, D, s["voice"], s["voice_rate"], workdir / "voice0", cancel,
                                     lambda p: job.set_stage("voice", p))
        fit = voice.fit_plan_dub(vinfo["duration"], D)
        script = dict(script, segments=items, mode="dub")
    else:
        # 3b) script vendeur ; on réécrit le script tant que la durée est trop éloignée de la vidéo
        job.set_stage("script", 0)
        if job.script:                                       # montage refait : le script existe déjà
            script = scriptgen.normalize(job.script)
        else:
            script = scriptgen.generate(frames, meta, D, s, ctx)
        job.set_stage("script", 1, "Script écrit")
        vinfo = None
        for attempt in range(3):
            job.set_stage("voice", 0, "Génération de la voix off" + (f" (essai {attempt + 1})" if attempt else ""))
            vinfo = voice.build(script["segments"], s["voice"], s["voice_rate"], workdir / f"voice{attempt}", cancel,
                                lambda p: job.set_stage("voice", p))
            words = sum(scriptgen.count_words(x["text"]) for x in script["segments"])
            if attempt == 0:
                measured = words / vinfo["duration"]
                config.save_settings({"wps": round(0.5 * float(s.get("wps", 3.7)) + 0.5 * measured, 3)})
            ratio = vinfo["duration"] / D
            if 0.92 <= ratio <= 1.06 or attempt == 2:
                break
            job.set_stage("fit", 0.5, f"Ajustement du script ({vinfo['duration']:.0f} s → {D:.0f} s)")
            script = scriptgen.adjust(script, vinfo["duration"], D, s, ctx)
    if cancel():
        raise media.Cancelled()

    # 4) montage
    job.set_stage("render", 0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}_{job.index:02d}_{job.id}"
    out = config.OUTPUTS / f"{name}.mp4"
    res = compose.compose(src, vinfo, script["segments"], out, workdir, s, seed=int(job.id, 16) % 10_000,
                          on_progress=lambda p: job.set_stage("render", p), should_cancel=cancel, fit=fit)

    # 5) fin
    job.set_stage("final", 0)
    compose.thumbnail(out, config.OUTPUTS / f"{name}.jpg")
    full_text = " ".join(x["text"] for x in script["segments"])
    record = dict(
        id=job.id, name=name, file=f"{name}.mp4", thumb=f"{name}.jpg", url=job.url, product=script.get("product", ""),
        caption=(script.get("caption") or (job.script or {}).get("caption") or ""), original_caption=(meta.get("description") or meta.get("title") or ""),
        script=full_text, segments=script["segments"], duration=round(res["duration"], 2),
        source_duration=round(res["source_duration"], 2), voice_speed=round(res["atempo"], 3),
        video_speed=round(1 / res["video_factor"], 3), created=datetime.now().isoformat(timespec="seconds"),
        mode="dub" if dub else "sales", source_language=((transcript or {}).get("language") if dub and transcript else (job.script or {}).get("source_language", "")) or "",
        original_text=" ".join(x["text"] for x in (transcript or {}).get("segments", [])) or (job.script or {}).get("original_text", ""),
    )
    (config.OUTPUTS / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
    job.result = record
