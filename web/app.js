"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const RECOMMENDED = { height_pct: 34, right_pct: 15, bottom_pct: 19 };

let state = { jobs: [], history: [], settings: null, ready: {} };
let count = 5;
let pollTimer = null;
let submitting = false;
let viewerName = null;
let viewerRec = null;

/* ---------- réseau ---------- */
async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  let data = {};
  try { data = await r.json(); } catch (e) { /* vide */ }
  if (!r.ok) { const err = new Error(data.error || r.statusText); err.data = data; throw err; }
  return data;
}

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  kids.flat().forEach(c => c != null && n.append(c));
  return n;
}

/* ---------- étape 1 / 2 ---------- */
function setCount(n) {
  count = Math.min(50, Math.max(1, parseInt(n, 10) || 1));
  $("#countInput").value = count;
}
function buildLinks(n, keep = []) {
  const ol = $("#links");
  const old = $$("input", ol).map(i => i.value);
  ol.innerHTML = "";
  for (let i = 0; i < n; i++) {
    const input = el("input", { type: "text", placeholder: "https://www.tiktok.com/@…/video/…", autocomplete: "off", spellcheck: "false", "aria-label": `Lien de la vidéo ${i + 1}` });
    input.value = (keep[i] ?? old[i]) || "";
    input.addEventListener("paste", onPaste);
    input.addEventListener("input", refreshLinksState);
    ol.append(el("li", {}, input));
  }
  $("#linksTitle").textContent = n === 1 ? "Colle le lien de la vidéo" : `Colle les ${n} liens (un par case)`;
  $("#generate").textContent = n === 1 ? "Générer la vidéo" : `Générer les ${n} vidéos`;
  refreshLinksState();
}
function onPaste(e) {
  const text = (e.clipboardData || window.clipboardData).getData("text") || "";
  const parts = text.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean);
  if (parts.length < 2) return;
  e.preventDefault();
  const inputs = $$("#links input");
  const start = inputs.indexOf(e.target);
  const need = start + parts.length;
  if (need > count) { setCount(need); buildLinks(count); }
  const fresh = $$("#links input");
  parts.forEach((p, i) => { fresh[start + i].value = p; });
  refreshLinksState();
}
function validUrl(v) { return /^https?:\/\/\S+$/i.test(v) || v.startsWith("/") || v.startsWith("~/") || v.startsWith("file://"); }
function refreshLinksState() {
  const inputs = $$("#links input");
  let filled = 0;
  inputs.forEach(i => {
    const v = i.value.trim();
    const ok = v && validUrl(v);
    i.parentElement.classList.toggle("ok", !!ok);
    i.classList.toggle("invalid", !!v && !ok);
    if (ok) filled++;
  });
  $("#linksHint").textContent = `${filled} lien${filled > 1 ? "s" : ""} sur ${inputs.length}`;
}

async function generate() {
  if (submitting) return;
  const inputs = $$("#links input");
  const urls = inputs.map(i => i.value.trim());
  const bad = inputs.filter(i => i.value.trim() && !validUrl(i.value.trim()));
  if (bad.length) { bad[0].focus(); $("#linksHint").textContent = "Un des liens n'a pas l'air valide (il doit commencer par https://)."; return; }
  const list = urls.filter(Boolean);
  if (!list.length) { inputs[0].focus(); $("#linksHint").textContent = "Colle au moins un lien."; return; }
  submitting = true; $("#generate").disabled = true;
  try {
    await api("/api/generate", { method: "POST", body: { urls: list } });
    $("#step2").hidden = true; $("#step1").hidden = false;
    $("#links").innerHTML = "";
    await refresh();
    $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    if (e.message === "no_api_key") { openSettings(true); }
    else alert(e.message);
  } finally { submitting = false; $("#generate").disabled = false; }
}

/* ---------- progression ---------- */
const PILL = { queued: "En attente", running: "En cours", done: "Terminée", error: "Échec", cancelled: "Annulée" };
const cards = new Map();

function fmtPct(p) { return Math.round(p * 100) + " %"; }
function fmtDate(iso) {
  try { return new Date(iso).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; }
}

function renderJobs() {
  const box = $("#jobs");
  const jobs = state.jobs;
  $("#progress").hidden = jobs.length === 0;
  const seen = new Set();
  jobs.forEach(j => {
    seen.add(j.id);
    let c = cards.get(j.id);
    if (!c) { c = buildCard(j); cards.set(j.id, c); box.append(c.root); }
    updateCard(c, j);
  });
  for (const [id, c] of cards) if (!seen.has(id)) { c.root.remove(); cards.delete(id); }

  // barre globale
  const live = jobs.filter(j => j.status !== "cancelled");
  const total = live.length || 1;
  const sum = live.reduce((a, j) => a + (j.status === "done" ? 1 : j.status === "error" ? 1 : j.progress), 0);
  const done = jobs.filter(j => j.status === "done").length;
  const failed = jobs.filter(j => j.status === "error").length;
  const active = jobs.some(j => j.status === "queued" || j.status === "running");
  const pct = Math.min(1, sum / total);
  const gb = $("#globalBar");
  $("i", gb).style.width = (pct * 100).toFixed(1) + "%";
  $("#globalPct").textContent = fmtPct(pct);
  gb.setAttribute("aria-valuenow", Math.round(pct * 100));
  gb.classList.toggle("running", active);
  gb.classList.toggle("done", !active && !failed && done > 0);
  $("#globalTitle").textContent = active ? "Génération en cours…" : failed ? "Terminé, avec quelques échecs" : "Toutes les vidéos sont prêtes 🎉";
  $("#globalSub").textContent = `${done} terminée${done > 1 ? "s" : ""} sur ${jobs.length}` + (failed ? ` · ${failed} en échec` : "");
  const waiting = jobs.filter(j => j.waiting);
  $("#waitBanner").hidden = waiting.length === 0;
  $("#waitText").textContent = waiting.length ? `${waiting.length} script${waiting.length > 1 ? "s" : ""} en attente.` : "";
  $("#cancelAll").hidden = !active;
  $("#clearDone").hidden = active;
}

function buildCard(j) {
  const media = el("div", { class: "job-media" }, el("span", { class: "num", text: String(j.index) }));
  const pill = el("span", { class: "pill" });
  const url = el("p", { class: "job-url" });
  const spin = el("span", { class: "spinner" });
  const label = el("span", { class: "label" });
  const pct = el("span", { class: "pct" });
  const bar = el("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, el("i"));
  const err = el("p", { class: "job-error", hidden: true });
  const extra = el("div", { class: "job-extra" });
  const actions = el("div", { class: "job-actions" });
  const root = el("article", { class: "job", "data-id": j.id },
    media,
    el("div", { class: "job-body" },
      el("div", { class: "job-head" }, el("h3", { text: `Vidéo ${j.index}` }), pill),
      url, el("div", { class: "job-stage" }, spin, label, pct), bar, err, extra, actions));
  return { root, media, pill, url, spin, label, pct, bar, err, extra, actions, status: null };
}

function updateCard(c, j) {
  c.url.textContent = j.url; c.url.title = j.url;
  c.pill.textContent = j.waiting ? "Claude écrit…" : (PILL[j.status] || j.status);
  c.pill.className = "pill " + (j.waiting ? "waiting" : j.status);
  c.label.textContent = j.status === "error" ? "Échec" : j.label;
  c.pct.textContent = j.status === "done" ? "100 %" : fmtPct(j.progress);
  $("i", c.bar).style.width = (j.status === "done" ? 100 : j.progress * 100).toFixed(1) + "%";
  c.bar.setAttribute("aria-valuenow", Math.round(j.progress * 100));
  c.bar.className = "bar" + (j.status === "running" ? " running" : j.status === "done" ? " done" : j.status === "error" ? " error" : "");
  c.spin.hidden = j.status !== "running";
  if (c.status === j.status) return;
  c.status = j.status;
  c.err.hidden = j.status !== "error";
  c.err.textContent = j.error || "";
  c.actions.innerHTML = ""; c.extra.innerHTML = "";
  if (j.status === "queued" || j.status === "running") {
    c.actions.append(el("button", { class: "btn ghost", type: "button", text: "Annuler", onclick: () => api(`/api/jobs/${j.id}/cancel`, { method: "POST" }).then(refresh) }));
  }
  if (j.status === "error" || j.status === "cancelled") {
    c.actions.append(el("button", { class: "btn", type: "button", text: "Réessayer", onclick: async () => { await api(`/api/jobs/${j.id}/retry`, { method: "POST" }); refresh(); } }));
  }
  if (j.status === "done" && j.result) {
    const r = j.result;
    c.media.innerHTML = "";
    c.media.append(el("video", { src: `/outputs/${r.file}`, poster: `/outputs/${r.thumb}`, controls: true, playsinline: true, preload: "metadata" }));
    c.extra.append(
      el("div", { class: "meta" },
        el("span", { text: `${r.duration.toFixed(1).replace(".", ",")} s` }),
        el("span", { text: `voix ×${r.voice_speed.toFixed(2).replace(".", ",")}` }),
        el("span", { text: `vidéo ×${r.video_speed.toFixed(2).replace(".", ",")}` })),
      el("details", { class: "job-script" }, el("summary", { text: "Voir le script" }), el("div", { class: "script", text: r.script })));
    c.actions.append(
      el("a", { class: "btn", href: `/outputs/${r.file}`, download: r.file, text: "Télécharger" }),
      el("button", { class: "btn primary", type: "button", text: "Poster sur TikTok", title: "Affiche un QR code à scanner avec ton téléphone", onclick: () => openPost(r) }),
      el("button", { class: "btn ghost", type: "button", text: "Voir dans le Finder", onclick: () => api("/api/reveal", { method: "POST", body: { name: r.file } }) }),
      el("button", { class: "btn ghost", type: "button", text: "Refaire le montage", title: "Même lien, même script, animation recalculée", onclick: async () => { await api(`/api/jobs/${j.id}/retry`, { method: "POST" }); refresh(); } }));
  }
}

/* ---------- poster sur TikTok (QR code) ---------- */
function captionFor(r) {
  if (r.caption) return r.caption;
  const tags = (state.settings && state.settings.hashtags) || "";
  return `${r.product || "Vidéo"} 🔥 Clique sur le panier orange 🛒 ${tags}`.trim();
}
let postRec = null, postToken = null, captionTimer = null;
async function openPost(r) {
  postRec = r; postToken = null;
  $("#postTitle").textContent = `Poster « ${r.product || "la vidéo"} » sur TikTok`;
  $("#postQr").innerHTML = ""; $("#postMode").textContent = "Création du QR code…";
  $("#postCaption").value = r.caption || captionFor(r);
  $("#postModal").showModal();
  try {
    const d = await api("/api/share", { method: "POST", body: { name: r.file, caption: $("#postCaption").value } });
    postToken = d.token;
    $("#postQr").innerHTML = d.qr;
    $("#postCaption").value = d.caption;
    $("#postMode").textContent = d.mode === "public" ? "Lien sécurisé : ouverture directe dans TikTok, même en 4G/5G" : "Téléphone et ordinateur sur le même Wi-Fi";
    $("#postTunnel").hidden = d.mode === "public";
    $("#postTunnelMsg").hidden = true;
  } catch (e) { $("#postMode").textContent = "Impossible de créer le QR code : " + e.message; }
}
/* ---------- historique ---------- */
function renderHistory() {
  const live = new Set(state.jobs.map(j => j.id));
  const items = state.history.filter(h => !live.has(h.id));
  $("#historySec").hidden = items.length === 0;
  $("#histCount").textContent = items.length ? `${items.length} vidéo${items.length > 1 ? "s" : ""}` : "";
  const box = $("#hist");
  const sig = items.map(i => i.name).join("|");
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = "";
  items.forEach(h => {
    box.append(el("button", { class: "hcard", type: "button", onclick: () => openViewer(h) },
      el("div", { class: "th" }, el("img", { src: `/outputs/${h.thumb}`, alt: "", loading: "lazy" })),
      el("div", {}, el("b", { text: h.product || "Vidéo générée" }), el("small", { text: `${fmtDate(h.created)} · ${h.duration.toFixed(0)} s` }))));
  });
}
function openViewer(h) {
  viewerName = h.name; viewerRec = h;
  const v = $("#viewerVideo");
  v.src = `/outputs/${h.file}`; v.poster = `/outputs/${h.thumb}`;
  $("#viewerTitle").textContent = h.product || "Vidéo générée";
  $("#viewerMeta").textContent = `${fmtDate(h.created)} · ${h.duration.toFixed(1).replace(".", ",")} s`;
  $("#viewerScript").textContent = h.script;
  const dl = $("#viewerDl"); dl.href = `/outputs/${h.file}`; dl.download = h.file;
  $("#viewer").showModal();
}

/* ---------- réglages ---------- */
function applyLayoutPreview(lo) {
  const s = $("#screen");
  s.style.setProperty("--h", lo.height_pct); s.style.setProperty("--r", lo.right_pct); s.style.setProperty("--b", lo.bottom_pct);
  $("#oHeight").textContent = lo.height_pct + " %"; $("#oRight").textContent = lo.right_pct + " %"; $("#oBottom").textContent = lo.bottom_pct + " %";
}
function currentLayout() {
  return { height_pct: +$("#sHeight").value, right_pct: +$("#sRight").value, bottom_pct: +$("#sBottom").value };
}
function fillSettings() {
  const s = state.settings; if (!s) return;
  const sel = $("#voice");
  if (!sel.options.length) s.voices.forEach(v => sel.append(el("option", { value: v.id, text: v.label })));
  sel.value = s.voice;
  $("#voiceRate").value = s.voice_rate;
  $("#mirror").checked = !!s.mirror;
  $("#concurrency").value = String(s.concurrency);
  $("#extra").value = s.extra_instructions || "";
  $("#hashtags").value = s.hashtags || "";
  $("#model").value = s.model;
  $("#apiKey").value = "";
  $("#apiKey").placeholder = s.has_api_key ? `Clé enregistrée (${s.api_key_hint}) — colle une autre clé pour la remplacer` : "sk-ant-…";
  $("#keyStatus").textContent = s.has_api_key ? "Une clé est enregistrée." : "";
  $("#keyStatus").className = "small";
  $("#sHeight").value = s.layout.height_pct; $("#sRight").value = s.layout.right_pct; $("#sBottom").value = s.layout.bottom_pct;
  applyLayoutPreview(s.layout);
}
function openSettings(needKey) {
  fillSettings();
  $("#saveStatus").textContent = "";
  $("#settings").showModal();
  if (needKey) {
    const fs = $("#fsKey"); fs.classList.remove("attention"); void fs.offsetWidth; fs.classList.add("attention");
    $("#keyStatus").textContent = "Colle ta clé API Anthropic pour pouvoir générer les scripts.";
    $("#keyStatus").className = "small bad";
    $("#apiKey").focus();
  }
}
async function saveSettings() {
  const body = {
    voice: $("#voice").value, voice_rate: $("#voiceRate").value, mirror: $("#mirror").checked,
    concurrency: +$("#concurrency").value, extra_instructions: $("#extra").value, model: $("#model").value.trim() || "claude-opus-5-5",
    layout: currentLayout(), hashtags: $("#hashtags").value.trim(),
  };
  const key = $("#apiKey").value.trim();
  if (key) body.anthropic_api_key = key;
  state.settings = await api("/api/settings", { method: "POST", body });
  $("#saveStatus").textContent = "Enregistré ✓";
  await refresh();
  fillSettings();
}

function renderTop() {
  const chip = $("#keyChip");
  const api = state.ready.api_key;
  chip.className = "chip ok";
  chip.textContent = api ? "● Scripts automatiques (clé API)" : "● Scripts écrits par Claude";
  chip.title = api ? "Une clé API Anthropic est enregistrée." : "Sans clé API, Claude écrit les scripts dans la conversation.";
  chip.onclick = () => openSettings(false);
}

/* ---------- boucle ---------- */
async function refresh() {
  try {
    state = await api("/api/state");
  } catch (e) { return; }
  renderTop(); renderJobs(); renderHistory();
  const active = state.jobs.some(j => j.status === "queued" || j.status === "running");
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refresh, active ? 700 : 4000);
}

/* ---------- événements ---------- */
function init() {
  $("#minus").onclick = () => setCount(count - 1);
  $("#plus").onclick = () => setCount(count + 1);
  $("#countInput").addEventListener("change", e => setCount(e.target.value));
  $("#countInput").addEventListener("keydown", e => { if (e.key === "Enter") $("#toLinks").click(); });
  $("#toLinks").onclick = () => {
    setCount($("#countInput").value);
    buildLinks(count);
    $("#step1").hidden = true; $("#step2").hidden = false;
    $("#links input").focus();
  };
  $("#changeCount").onclick = () => { $("#step2").hidden = true; $("#step1").hidden = false; $("#countInput").focus(); };
  $("#generate").onclick = generate;
  $("#cancelAll").onclick = async () => { for (const j of state.jobs) if (j.status === "queued" || j.status === "running") await api(`/api/jobs/${j.id}/cancel`, { method: "POST" }); refresh(); };
  $("#clearDone").onclick = async () => { await api("/api/jobs/clear", { method: "POST" }); refresh(); };

  $("#openSettings").onclick = () => openSettings(false);
  $("#closeSettings").onclick = () => $("#settings").close();
  $("#saveSettings").onclick = () => saveSettings().catch(e => { $("#saveStatus").textContent = e.message; });
  ["sHeight", "sRight", "sBottom"].forEach(id => $("#" + id).addEventListener("input", () => applyLayoutPreview(currentLayout())));
  $("#resetLayout").onclick = () => { $("#sHeight").value = RECOMMENDED.height_pct; $("#sRight").value = RECOMMENDED.right_pct; $("#sBottom").value = RECOMMENDED.bottom_pct; applyLayoutPreview(RECOMMENDED); };
  $("#testKey").onclick = async () => {
    const st = $("#keyStatus"); st.className = "small"; st.textContent = "Vérification…";
    try {
      const key = $("#apiKey").value.trim();
      if (key) state.settings = await api("/api/settings", { method: "POST", body: { anthropic_api_key: key } });
      const r = await api("/api/test-key", { method: "POST" });
      st.textContent = r.message; st.className = "small " + (r.ok ? "ok" : "bad");
      refresh();
    } catch (e) { st.textContent = e.message; st.className = "small bad"; }
  };
  $("#playVoice").onclick = async () => {
    const b = $("#playVoice"); b.disabled = true; const t = b.textContent; b.textContent = "…";
    try {
      const a = $("#voiceAudio"); a.src = `/api/voice-sample?voice=${encodeURIComponent($("#voice").value)}&t=${Date.now()}`;
      await a.play();
    } catch (e) { /* silencieux */ } finally { b.disabled = false; b.textContent = t; }
  };

  $("#viewerClose").onclick = () => $("#viewer").close();
  $("#viewer").addEventListener("close", () => { $("#viewerVideo").pause(); });
  $("#viewerRerender").onclick = async () => {
    if (!viewerName) return;
    await api("/api/rerender", { method: "POST", body: { name: viewerName } });
    $("#viewer").close(); refresh(); $("#progress").scrollIntoView({ behavior: "smooth" });
  };
  $("#viewerAssist").onclick = () => { if (viewerRec) { const r = viewerRec; $("#viewer").close(); openPost(r); } };
  $("#postTunnel").onclick = async () => {
    const msg = $("#postTunnelMsg"), btn = $("#postTunnel");
    btn.disabled = true; msg.hidden = false; msg.textContent = "Préparation… (la première fois, un petit outil gratuit de Cloudflare est téléchargé)";
    await api("/api/tunnel/start", { method: "POST" });
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const st = (await api("/api/state")).tunnel || {};
      msg.textContent = st.message || "…";
      if (st.state === "up") { btn.disabled = false; if (postRec) openPost(postRec); return; }
      if (st.state === "error") { btn.disabled = false; return; }
    }
    btn.disabled = false;
  };
  $("#postClose").onclick = () => $("#postModal").close();
  $("#postCopy").onclick = async e => {
    const b = e.currentTarget, t = b.textContent;
    try { await navigator.clipboard.writeText($("#postCaption").value); } catch (err) { $("#postCaption").select(); document.execCommand("copy"); }
    b.textContent = "Copiée ✓"; setTimeout(() => { b.textContent = t; }, 1600);
  };
  $("#postCaption").addEventListener("input", () => {
    clearTimeout(captionTimer);
    captionTimer = setTimeout(() => postToken && api("/api/share/caption", { method: "POST", body: { token: postToken, caption: $("#postCaption").value } }), 400);
  });
  $("#postModal").addEventListener("click", e => { if (e.target === $("#postModal")) $("#postModal").close(); });
  $("#viewerReveal").onclick = () => viewerName && api("/api/reveal", { method: "POST", body: { name: viewerName + ".mp4" } });
  $("#viewerDelete").onclick = async () => {
    if (!viewerName || !confirm("Supprimer définitivement cette vidéo ?")) return;
    await api(`/api/history/${viewerName}`, { method: "DELETE" });
    $("#viewer").close(); $("#hist").dataset.sig = ""; refresh();
  };
  [$("#settings"), $("#viewer")].forEach(d => d.addEventListener("click", e => { if (e.target === d) d.close(); }));

  setCount(5);
  refresh().then(() => { if (state.settings) $("#settings").dataset.loaded = "1"; });
}
init();
