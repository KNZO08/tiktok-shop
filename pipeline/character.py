"""Personnage animé : calques découpés (tête, mâchoire, bras…) + squelette simple.

Rig : torse (plaque) / tête (+ mâchoire, yeux, bouche) / 2 bras à 3 segments
(épaule → coude → poignet).  Les angles sont donnés « vers l'extérieur » (out) :
positif = le bras s'écarte du corps / l'avant-bras se lève.
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "character"
CANVAS_SRC = (-200, -60, 930, 797)       # zone de dessin (coordonnées « image source »)
FPS = 30


# --------------------------------------------------------------------------------------
#  Maths
# --------------------------------------------------------------------------------------
def T(tx, ty):
    return np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1]], np.float64)


def S(sx, sy=None):
    sy = sx if sy is None else sy
    return np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], np.float64)


def R(angle_deg, c):
    """Rotation autour de c ; angle > 0 = sens anti-horaire à l'écran."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return T(c[0], c[1]) @ np.array([[ca, sa, 0], [-sa, ca, 0], [0, 0, 1]], np.float64) @ T(-c[0], -c[1])


def smoothstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


# --------------------------------------------------------------------------------------
#  Calques
# --------------------------------------------------------------------------------------
class Piece:
    def __init__(self, rgba_u8, origin, scale):
        self.origin = np.array(origin, np.float64)
        self.scale = scale
        f = rgba_u8.astype(np.float32) / 255.0
        f[..., :3] *= f[..., 3:4]                       # alpha prémultiplié
        bgra = f[..., [2, 1, 0, 3]].copy()
        if abs(scale - 1) > 1e-3:
            h, w = bgra.shape[:2]
            interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            bgra = cv2.resize(bgra, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=interp)
        self.img = np.ascontiguousarray(bgra)

    def draw(self, canvas, M, canvas_origin):
        """M : transformation source→source ; dessine dans canvas (pixels)."""
        s = self.scale
        A = S(s) @ T(-canvas_origin[0], -canvas_origin[1]) @ M @ T(*self.origin) @ S(1 / s)
        h, w = self.img.shape[:2]
        pts = A @ np.array([[0, w, w, 0], [0, 0, h, h], [1, 1, 1, 1]], np.float64)
        x0 = int(max(math.floor(pts[0].min()) - 1, 0)); y0 = int(max(math.floor(pts[1].min()) - 1, 0))
        x1 = int(min(math.ceil(pts[0].max()) + 1, canvas.shape[1])); y1 = int(min(math.ceil(pts[1].max()) + 1, canvas.shape[0]))
        if x1 <= x0 or y1 <= y0:
            return
        A2 = T(-x0, -y0) @ A
        roi = cv2.warpAffine(self.img, A2[:2], (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        dst = canvas[y0:y1, x0:x1]
        dst *= (1.0 - roi[..., 3:4])
        dst += roi


class Character:
    def __init__(self, scale=1.0):
        self.scale = scale
        rig = json.loads((ASSETS / "rig.json").read_text())
        self.rig = rig
        L = rig["layers"]

        def load(name):
            im = np.asarray(Image.open(ASSETS / f"{name}.png").convert("RGBA"))
            return im

        self.pieces = {n: Piece(load(n), o, scale) for n, o in L.items() if n != "mouth_mask"}
        self.head_origin = np.array(L["head"], np.float64)
        self.mouth_mask = load("mouth_mask")[..., 3]
        self.mouth_origin = np.array(L["mouth_mask"], np.float64)
        # le bas de la chemise (coupé net dans la capture d'origine) s'estompe doucement sous la veste
        plate = self.pieces["plate"]
        top = int(round((690 - plate.origin[1]) * scale)); bot = int(round((754 - plate.origin[1]) * scale))
        h_img = plate.img.shape[0]
        ramp = np.ones(h_img, np.float32)
        a, b = max(0, min(top, h_img)), max(0, min(bot, h_img))
        if b > a:
            ramp[a:b] = np.linspace(1, 0, b - a, dtype=np.float32)
        ramp[b:] = 0
        plate.img *= ramp[:, None, None]
        # disque de coude (sous les pièces) pour que l'articulation reste arrondie
        r = 23
        d = np.zeros((2 * r + 8, 2 * r + 8, 4), np.uint8)
        c = (r + 4, r + 4)
        cv2.circle(d, c, r, (*rig["sleeve_line"], 255), -1, cv2.LINE_AA)
        cv2.circle(d, c, r - 4, (*rig["sleeve_fill"], 255), -1, cv2.LINE_AA)
        self.elbow_disc = {
            side: Piece(d, (rig["arms"][side]["E"][0] - c[0], rig["arms"][side]["E"][1] - c[1]), scale)
            for side in "LR"
        }
        x0, y0, x1, y1 = CANVAS_SRC
        self.canvas_origin = (x0, y0)
        self.size = (int(round((x1 - x0) * scale)), int(round((y1 - y0) * scale)))   # (w, h)
        self.rest_bbox = (float(L["head"][0]), 64.0, 668.0, 797.0)   # x0, y0, x1, y1 du personnage au repos (source)
        self.skin = [tuple(int(v) for v in c[::-1]) for c in rig["skin_tones"]]       # BGR

    # ----------------------------------------------------------------------------------
    def _face(self, m, blink):
        """Tête avec yeux (clignement) et bouche dessinés."""
        base = self.pieces["head"]
        img = base.img.copy()
        s = self.scale
        ho = self.head_origin

        def to_local(p):
            return ((p[0] - ho[0]) * s, (p[1] - ho[1]) * s)

        def over(layer_bgr, alpha):
            """composite premult : alpha (h,w) 0..1 ; couleur plate"""
            a = alpha[..., None].astype(np.float32)
            src = np.concatenate([np.asarray(layer_bgr, np.float32) * a, a], axis=-1)
            img[...] = src + img * (1 - a)

        h, w = img.shape[:2]
        # --- yeux
        if blink > 0.02:
            for e, skin in zip(self.rig["eyes"], self.skin):
                cx, cy = to_local(e["center"]); rx, ry = (e["rx"] + 3) * s, (e["ry"] + 2) * s
                top = cy - ry; edge = top + 2 * ry * (0.12 + 0.78 * blink)
                m_ = np.zeros((h, w), np.uint8)
                cv2.ellipse(m_, (int(cx * 16), int(cy * 16)), (int(rx * 16), int(ry * 16)), 0, 0, 360, 255, -1, cv2.LINE_AA, 4)
                yy = np.arange(h, dtype=np.float32)[:, None]
                cover = np.clip((edge - yy) / max(1.5 * s, 1), 0, 1)
                alpha = (m_.astype(np.float32) / 255.0) * cover * (img[..., 3] > 0.5)
                col = np.array(skin, np.float32) / 255.0
                over(col, alpha)
                if blink > 0.35:   # trait de paupière
                    ln = np.zeros((h, w), np.uint8)
                    pts = np.array([[cx - rx * 0.8, edge - 0.4 * s], [cx, edge + 1.6 * s], [cx + rx * 0.8, edge - 0.3 * s]], np.float32)
                    cv2.polylines(ln, [(pts * 16).astype(np.int32)], False, 255, max(1, int(round(2.6 * s))), cv2.LINE_AA, 4)
                    over(np.array([0.08, 0.06, 0.07], np.float32), ln.astype(np.float32) / 255.0 * 0.95 * (alpha > 0.02))
        # --- bouche
        if m > 0.06:
            mo = self.rig["mouth"]
            cx, cy = to_local(mo["center"])
            rx = mo["rx"] * s * (0.72 + 0.28 * m)
            ry = max(1.0, mo["ry_max"] * s * m)
            cy = cy + 1.0 * s + 0.35 * ry
            mm = np.zeros((h, w), np.float32)
            # masque de la zone de peau (moustache/barbe exclues)
            mk = cv2.resize(self.mouth_mask, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR) if abs(s - 1) > 1e-3 else self.mouth_mask
            ox, oy = int(round((self.mouth_origin[0] - ho[0]) * s)), int(round((self.mouth_origin[1] - ho[1]) * s))
            hh, ww = mk.shape[:2]
            ys0, xs0 = max(oy, 0), max(ox, 0)
            ys1, xs1 = min(oy + hh, h), min(ox + ww, w)
            if ys1 > ys0 and xs1 > xs0:
                mm[ys0:ys1, xs0:xs1] = mk[ys0 - oy:ys1 - oy, xs0 - ox:xs1 - ox] / 255.0
            mm = cv2.GaussianBlur(mm, (0, 0), 0.6)
            ell = np.zeros((h, w), np.uint8)
            cv2.ellipse(ell, (int(cx * 16), int(cy * 16)), (int(rx * 16), int(ry * 16)), 0, 0, 360, 255, -1, cv2.LINE_AA, 4)
            ea = ell.astype(np.float32) / 255.0 * mm
            over(np.array([0.08, 0.05, 0.18], np.float32), ea)               # intérieur (BGR)
            yy = np.arange(h, dtype=np.float32)[:, None]
            if m > 0.3:   # dents du haut
                teeth = ea * np.clip((cy - ry + 0.34 * ry * 2 * min(1, m + 0.2) - yy) / 1.2, 0, 1)
                over(np.array([0.92, 0.93, 0.95], np.float32), teeth * 0.95)
            if m > 0.45:  # langue
                tongue = ea * np.clip((yy - (cy + ry * 0.25)) / 1.5, 0, 1)
                over(np.array([0.42, 0.40, 0.84], np.float32), tongue * 0.9)
        return img

    # ----------------------------------------------------------------------------------
    def render(self, pose):
        """pose -> image BGRA uint8 (alpha droit) de taille self.size."""
        w, h = self.size
        canvas = np.zeros((h, w, 4), np.float32)
        co = self.canvas_origin
        rig = self.rig
        bp = rig["body_pivot"]
        b = pose["body"]
        B = T(b["dx"], b["dy"]) @ R(b["rot"], bp) @ T(bp[0], bp[1]) @ S(b["sx"], b["sy"]) @ T(-bp[0], -bp[1])
        hp = rig["head_pivot"]
        hd = pose["head"]
        MH = B @ T(hd["dx"], hd["dy"]) @ R(hd["rot"], hp)

        self.pieces["plate"].draw(canvas, B, co)
        # tête
        face = Piece.__new__(Piece)
        face.origin = self.pieces["head"].origin; face.scale = self.scale
        face.img = self._face(pose["mouth"], pose["blink"])
        face.draw(canvas, MH, co)
        jaw_dy = 2.6 * pose["mouth"]
        self.pieces["jaw"].draw(canvas, MH @ T(0, jaw_dy), co)

        for side in "LR":
            sign = -1.0 if side == "L" else 1.0
            a1, a2, a3 = pose["arm" + side]
            Sx, Ex, Wx = (rig["arms"][side][k] for k in "SEW")
            M1 = B @ R(sign * a1, Sx)
            M2 = M1 @ R(sign * a2, Ex)
            M3 = M2 @ R(sign * a3, Wx)
            self.elbow_disc[side].draw(canvas, M1, co)
            self.pieces[f"arm{side}_upper"].draw(canvas, M1, co)
            self.pieces[f"arm{side}_fore"].draw(canvas, M2, co)
            self.pieces[f"arm{side}_hand"].draw(canvas, M3, co)

        # fondu du bas (la capture d'origine est coupée à hauteur de hanches)
        fade = pose.get("fade_rows")
        if fade:
            y_end = int(round((797 - co[1]) * self.scale))
            n = int(round(fade * self.scale))
            ramp = np.linspace(1, 0, n, dtype=np.float32)[:, None, None]
            y0 = max(y_end - n, 0)
            canvas[y0:y_end] *= ramp[: y_end - y0]
            canvas[y_end:] = 0
        a = canvas[..., 3:4]
        out = np.zeros_like(canvas)
        np.divide(canvas[..., :3], a, out=out[..., :3], where=a > 1e-4)
        out[..., 3:4] = a
        return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------------------
#  Gestes
# --------------------------------------------------------------------------------------
# (épaule, coude, poignet) par bras, en degrés « vers l'extérieur ».
# lean : inclinaison du corps (>0 vers la gauche de l'écran), head : inclinaison de la tête,
# beat : intensité des petits mouvements calés sur la voix, bounce : rebond du corps.
GESTURES = {
    "idle":     dict(L=(1, 3, 0),    R=(0, 2, 0),    lean=0.0,  head=0.0,  beat=0.4, bounce=0.5),
    "talk":     dict(L=(6, 18, 3),   R=(1, 5, 0),    lean=0.0,  head=0.0,  beat=1.0, bounce=1.0),
    "open":     dict(L=(24, 52, 14), R=(3, 10, 4),   lean=0.0,  head=-1.5, beat=0.8, bounce=1.0),
    "wow":      dict(L=(36, 82, 10), R=(4, 16, 4),   lean=0.0,  head=2.0,  beat=1.2, bounce=1.8),
    "point":    dict(L=(50, 6, 0),   R=(2, 6, 0),    lean=2.5,  head=3.0,  beat=0.8, bounce=1.0),
    "raise":    dict(L=(2, 8, 0),    R=(8, 146, 0),  lean=-1.5, head=-2.5, beat=0.7, bounce=0.8),
    "count":    dict(L=(3, 10, 0),   R=(6, 146, 6),  lean=-1.0, head=-1.5, beat=1.3, bounce=0.8, osc=(("R", 2, 6, 2.0),)),
    "wave":     dict(L=(2, 8, 0),    R=(8, 144, 0),  lean=-1.0, head=-2.0, beat=0.6, bounce=0.8, osc=(("R", 2, 22, 3.2),)),
    "shrug":    dict(L=(14, 48, 26), R=(3, 10, 12),  lean=0.0,  head=3.5,  beat=0.7, bounce=0.6, lift=-9),
    "emphasis": dict(L=(12, 36, 8),  R=(2, 8, 2),    lean=0.0,  head=0.0,  beat=1.7, bounce=1.5),
    "basket":   dict(L=(48, 8, -6),  R=(2, 6, 0),    lean=3.0,  head=4.0,  beat=0.9, bounce=1.0, osc=(("L", 0, 7, 2.4), ("L", 2, 10, 2.4))),
}
GESTURE_NAMES = list(GESTURES)


def _vec(g):
    return np.array([*g["L"], *g["R"], g["lean"], g["head"], g["beat"], g["bounce"], g.get("lift", 0.0)], np.float64)


class Animator:
    """Calcule la pose de chaque image à partir de la voix (enveloppe) et du script (gestes)."""

    def __init__(self, duration, env, segments, fps=FPS, seed=1):
        self.fps = fps
        self.n = int(math.ceil(duration * fps))
        self.duration = duration
        self.rng = np.random.default_rng(seed)
        env = np.asarray(env, np.float64)
        if len(env) < self.n:
            env = np.pad(env, (0, self.n - len(env)))
        self.env = env[: self.n]
        self.segments = segments or [dict(start=0.0, end=duration, gesture="talk")]
        self.mouth = self._mouth_curve(self.env)
        self.beats = self._beat_curve(self.env)
        self.blinks = self._blink_times()
        self.phase = self.rng.uniform(0, 2 * math.pi, 6)

    # -- voix -> bouche
    def _mouth_curve(self, env):
        e = np.clip(env, 0, 1) ** 0.75
        out = np.zeros_like(e)
        v = 0.0
        for i, x in enumerate(e):
            target = min(1.0, x * 1.25)
            k = 0.7 if target > v else 0.38          # ouverture rapide, fermeture un peu plus lente
            v += (target - v) * k
            out[i] = v
        jitter = 0.88 + 0.24 * np.sin(np.arange(len(e)) * 0.9 + self.rng.uniform(0, 6)) * np.sin(np.arange(len(e)) * 0.37)
        out = out * jitter
        out[out < 0.07] = 0.0
        return np.clip(out, 0, 1)

    # -- voix -> petits « coups » (accents)
    def _beat_curve(self, env):
        n = len(env)
        beats = np.zeros(n)
        last = -99
        low = True
        for i, x in enumerate(env):
            if x < 0.22:
                low = True
            if low and x > 0.5 and i - last > int(0.2 * self.fps):
                last = i
                low = False
                amp = 0.6 + 0.4 * min(1, x)
                for j in range(i, min(n, i + int(0.9 * self.fps))):
                    t = (j - i) / self.fps
                    beats[j] += amp * (t / 0.06 if t < 0.06 else math.exp(-(t - 0.06) / 0.22))
        return np.clip(beats, 0, 1.6)

    def _blink_times(self):
        t, out = float(self.rng.uniform(0.6, 1.6)), []
        while t < self.duration:
            out.append(t)
            t += float(self.rng.uniform(2.2, 4.6))
        return out

    def _blink(self, t):
        for b in self.blinks:
            d = t - b
            if 0 <= d < 0.16:
                return math.sin(math.pi * d / 0.16)
        return 0.0

    # -- geste actif (avec transitions douces)
    def _gesture_vec(self, t):
        segs = self.segments
        idx = 0
        for i, s in enumerate(segs):
            if t >= s["start"] - 0.12:
                idx = i
        cur = _vec(GESTURES.get(segs[idx]["gesture"], GESTURES["talk"]))
        if idx > 0:
            prev = _vec(GESTURES.get(segs[idx - 1]["gesture"], GESTURES["talk"]))
            w = smoothstep((t - (segs[idx]["start"] - 0.12)) / 0.34)
            cur = prev * (1 - w) + cur * w
        return cur, GESTURES.get(segs[idx]["gesture"], GESTURES["talk"])

    def pose(self, i):
        t = i / self.fps
        v, g = self._gesture_vec(t)
        beat = self.beats[min(i, self.n - 1)]
        mouth = self.mouth[min(i, self.n - 1)]
        bg, bb = v[8], v[9]
        ph = self.phase
        L = list(v[0:3]); Rr = list(v[3:6])
        # oscillations propres au geste (saluer, pointer…)
        for side, joint, amp, freq in g.get("osc", ()):
            arr = L if side == "L" else Rr
            arr[joint] += amp * math.sin(2 * math.pi * freq * t)
        # accents sur la voix
        L[1] += 9 * beat * bg; Rr[1] += 3 * beat * bg
        L[0] += 3.5 * beat * bg; Rr[0] += 1 * beat * bg
        # respiration / balancement
        sway = math.sin(2 * math.pi * 0.21 * t + ph[0])
        L[0] += 1.2 * math.sin(2 * math.pi * 0.31 * t + ph[1]); Rr[0] += 1.2 * math.sin(2 * math.pi * 0.27 * t + ph[2])
        L[1] += 1.8 * math.sin(2 * math.pi * 0.43 * t + ph[3]); Rr[1] += 1.8 * math.sin(2 * math.pi * 0.37 * t + ph[4])
        breath = math.sin(2 * math.pi * 0.36 * t + ph[5])
        lean = v[6]
        body = dict(
            dx=0.0,
            dy=-9.0 * beat * bb + v[10] - 1.5 * breath,
            rot=lean + 0.7 * sway + 0.6 * beat * (1 if int(t * 1.7) % 2 else -1) * 0,
            sx=1.0, sy=1.0 + 0.007 * breath + 0.012 * beat * bb,
        )
        head = dict(
            dx=0.0,
            dy=5.0 * beat * bb - 1.0 * breath,
            rot=v[7] + 1.4 * math.sin(2 * math.pi * 0.23 * t + ph[1]) + 1.2 * beat,
        )
        return dict(body=body, head=head, mouth=float(mouth), blink=self._blink(t), armL=L, armR=Rr)
