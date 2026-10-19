"""Chef d'orchestre de l'animation : transforme la voix + le script en poses image par image.

Style visé : celui d'Adobe « Animer des personnages » — bras qui se plient (main sur la hanche, sur le ventre,
paume ouverte), tête qui tourne, regard qui bouge, sourcils expressifs, mouvements doux avec un léger rebond.
"""
import math

import numpy as np

FPS = 30

# (épaule, coude, poignet) par bras, en degrés « vers l'extérieur » ; L = bras à gauche de l'écran, R = à droite.
POSES = {
    "hang":      dict(L=(3, 4, 2),      R=(3, 4, 2)),
    "relax":     dict(L=(8, 11, 4),     R=(5, 8, 3)),
    "belly_R":   dict(L=(5, 7, 2),      R=(6, -52, -8)),
    "belly_L":   dict(L=(7, -52, -8),   R=(4, 6, 2)),
    "hip_L":     dict(L=(30, -86, -14), R=(5, 7, 3)),
    "hip_R":     dict(L=(5, 7, 3),      R=(26, -84, -12)),
    "hips":      dict(L=(30, -86, -14), R=(26, -84, -12)),
    "explain_L": dict(L=(16, 92, 16),   R=(5, 7, 3)),
    "explain_R": dict(L=(5, 7, 3),      R=(10, 90, 14)),
    "palms":     dict(L=(20, 14, 18),   R=(12, 10, 14)),
    "wow":       dict(L=(36, 84, 10),   R=(10, 58, 8)),
    "point_L":   dict(L=(50, 6, 0),     R=(5, 7, 3)),
    "shrug":     dict(L=(16, 54, 28),   R=(8, 38, 22), lift=-8.0),
    "basket":    dict(L=(48, 8, -6),    R=(4, 8, 2), osc=(("L", 0, 7, 2.4), ("L", 2, 10, 2.4))),
}
TALK_POOL = ["relax", "belly_R", "belly_L", "hip_L", "hip_R", "explain_L", "explain_R", "relax", "hips"]

# tag du script -> poses possibles + « ambiance » (sourcils, inclinaison, intensité des accents)
GESTURES = {
    "idle":     dict(poses=["relax", "hang", "belly_R"], brow=0.0, beat=0.5),
    "talk":     dict(poses=TALK_POOL, brow=0.0, beat=1.0),
    "open":     dict(poses=["palms", "explain_L", "explain_R"], brow=0.25, beat=0.9),
    "wow":      dict(poses=["wow", "palms", "explain_L"], brow=0.9, beat=1.3),
    "point":    dict(poses=["point_L", "explain_L"], brow=0.2, beat=0.9),
    "raise":    dict(poses=["explain_R", "explain_L"], brow=0.5, beat=0.9),
    "count":    dict(poses=["explain_L", "hip_R", "belly_R", "explain_R"], brow=0.2, beat=1.2),
    "wave":     dict(poses=["explain_R", "explain_L"], brow=0.5, beat=0.8),
    "shrug":    dict(poses=["shrug", "palms"], brow=0.7, beat=0.8),
    "emphasis": dict(poses=["explain_L", "wow", "palms", "hip_R"], brow=0.5, beat=1.5),
    "basket":   dict(poses=["basket"], brow=0.35, beat=1.0),
}
GESTURE_NAMES = list(GESTURES)


def spring(targets, omega, zeta, dt):
    """Suit `targets` (n, k) avec un ressort amorti : mouvement doux avec léger rebond."""
    targets = np.asarray(targets, np.float64)
    x = targets[0].copy()
    v = np.zeros_like(x)
    out = np.empty_like(targets)
    om = np.broadcast_to(np.asarray(omega, np.float64), x.shape)
    ze = np.broadcast_to(np.asarray(zeta, np.float64), x.shape)
    for i, tg in enumerate(targets):
        a = om * om * (tg - x) - 2 * ze * om * v
        v += a * dt
        x += v * dt
        out[i] = x
    return out


def smooth_noise(n, rng, period, amp):
    """Bruit lent (somme de sinus) : petits mouvements de vie."""
    t = np.arange(n) / FPS
    out = np.zeros(n)
    for k in range(3):
        out += np.sin(2 * math.pi * t / (period * (0.6 + 0.45 * k)) + rng.uniform(0, 6.28)) / (k + 1)
    return out * amp / 1.8


class Animator:
    def __init__(self, duration, env, segments, fps=FPS, seed=1, wide=None):
        self.fps = fps
        self.n = int(math.ceil(duration * fps))
        self.duration = duration
        self.rng = np.random.default_rng(seed)
        n = self.n
        env = np.asarray(env, np.float64)
        env = np.pad(env, (0, max(0, n - len(env))))[:n]
        self.env = env
        self.segments = segments or [dict(start=0.0, end=duration, gesture="talk")]
        self.mouth = self._mouth_curve(env)
        self.wide = self._wide_curve(wide, n)
        self.beats = self._beat_curve(env)
        self._plan()

    # ------------------------------------------------------------------ bouche / accents
    def _mouth_curve(self, env):
        e = np.clip(env, 0, 1) ** 0.7
        out = np.zeros_like(e)
        v = 0.0
        for i, x in enumerate(e):
            target = min(1.0, x * 1.3)
            k = 0.75 if target > v else 0.4
            v += (target - v) * k
            out[i] = v
        t = np.arange(len(e))
        jitter = 0.86 + 0.26 * np.sin(t * 0.9 + self.rng.uniform(0, 6)) * np.sin(t * 0.37)
        out = out * jitter
        out[out < 0.07] = 0.0
        return np.clip(out, 0, 1)

    def _wide_curve(self, wide, n):
        if wide is None:
            t = np.arange(n)
            w = 0.5 * np.sin(t * 0.55 + self.rng.uniform(0, 6)) + 0.35 * np.sin(t * 0.23)
        else:
            w = np.pad(np.asarray(wide, np.float64), (0, max(0, n - len(wide))))[:n]
        return np.clip(spring(w[:, None], 22.0, 0.9, 1.0 / FPS)[:, 0], -1, 1)

    def _beat_curve(self, env):
        n = len(env)
        beats = np.zeros(n)
        last, low = -99, True
        for i, x in enumerate(env):
            if x < 0.22:
                low = True
            if low and x > 0.5 and i - last > int(0.22 * self.fps):
                last, low = i, False
                amp = 0.6 + 0.4 * min(1, x)
                for j in range(i, min(n, i + int(0.9 * self.fps))):
                    t = (j - i) / self.fps
                    beats[j] += amp * (t / 0.07 if t < 0.07 else math.exp(-(t - 0.07) / 0.2))
        return np.clip(beats, 0, 1.5)

    # ------------------------------------------------------------------ planification
    def _seg_at(self, t):
        idx = 0
        for i, s in enumerate(self.segments):
            if t >= s["start"] - 0.2:
                idx = i
        return idx

    def _plan(self):
        n, fps, rng = self.n, self.fps, self.rng
        dt = 1.0 / fps
        # 1) calendrier des poses de bras
        events = [(0.0, "relax")]
        cur = "relax"

        def choose(pool, avoid):
            opts = [p for p in pool if p != avoid] or pool
            return opts[int(rng.integers(len(opts)))]

        for i, seg in enumerate(self.segments):
            g = GESTURES.get(seg["gesture"], GESTURES["talk"])
            t0 = max(0.0, seg["start"] - 0.12)
            if seg["gesture"] == "basket" or i == 0 or seg["start"] - events[-1][0] > 0.9:
                cur = choose(g["poses"], cur)
                if t0 > events[-1][0]:
                    events.append((t0, cur))
            # longue phrase : on change de pose en cours de route
            t = t0 + float(rng.uniform(1.9, 3.1))
            while t < seg["end"] - 0.9 and seg["gesture"] != "basket":
                cur = choose(g["poses"] + TALK_POOL[:4], cur)
                events.append((t, cur))
                t += float(rng.uniform(1.9, 3.2))
        events.sort()
        names = []
        k = 0
        for i in range(n):
            t = i * dt
            while k + 1 < len(events) and events[k + 1][0] <= t:
                k += 1
            names.append(events[k][1])
        arm_t = np.zeros((n, 6)); lift = np.zeros(n); osc = [None] * n
        for i, nm in enumerate(names):
            p = POSES[nm]
            arm_t[i] = [*p["L"], *p["R"]]
            lift[i] = p.get("lift", 0.0)
            osc[i] = p.get("osc")
        om = np.array([9.5, 10.5, 7.5, 9.5, 10.5, 7.5])
        ze = np.array([0.6, 0.55, 0.45, 0.6, 0.55, 0.45])
        arms = spring(arm_t, om, ze, dt)
        self.lift = spring(lift[:, None], 10.0, 0.7, dt)[:, 0]
        # oscillations propres à une pose (taper sur le panier…)
        t = np.arange(n) / fps
        for i in range(n):
            if osc[i]:
                for side, joint, amp, freq in osc[i]:
                    arms[i, (0 if side == "L" else 3) + joint] += amp * math.sin(2 * math.pi * freq * t[i])
        # 2) accents sur la voix + vie
        beat = self.beats
        flavor = np.array([GESTURES.get(self.segments[self._seg_at(ti)]["gesture"], GESTURES["talk"])["beat"] for ti in t])
        arms[:, 1] += 7 * beat * flavor; arms[:, 4] += 3 * beat * flavor
        arms[:, 0] += 2.5 * beat * flavor; arms[:, 3] += 1 * beat * flavor
        for j in range(6):
            arms[:, j] += smooth_noise(n, rng, 2.6, 1.6)
        self.arms = arms
        # 3) tête : orientation (yaw), regard, inclinaison, sourcils
        def steps(tmin, tmax, sampler, extra=2.0):
            out = np.zeros(n)
            tt = 0.0
            while tt < self.duration + extra:
                out[int(tt * fps):] = sampler()
                tt += float(rng.uniform(tmin, tmax))
            return out

        yaw_t = steps(1.4, 3.4, lambda: float(np.clip(rng.normal(0, 0.62), -1, 1)))
        tilt_t = steps(1.6, 3.6, lambda: float(np.clip(rng.normal(0, 3.6), -8, 8)))
        for i in range(n):                                     # la phrase finale : on regarde « vers le panier » (gauche)
            seg = self.segments[self._seg_at(i * dt)]
            if seg["gesture"] == "basket":
                yaw_t[i] = -0.8; tilt_t[i] = 4.0
        self.yaw = spring(yaw_t[:, None], 3.6, 0.85, dt)[:, 0]
        self.tilt = spring(tilt_t[:, None], 4.5, 0.8, dt)[:, 0] + smooth_noise(n, rng, 3.1, 1.0)
        # regard : suit un peu la tête + petits sauts (saccades)
        gx_t = steps(0.7, 2.3, lambda: float(np.clip(rng.normal(0, 1.9), -3.5, 3.5)))
        gy_t = steps(0.7, 2.3, lambda: float(np.clip(rng.normal(0, 0.9), -1.6, 1.6)))
        gaze = spring(np.stack([gx_t + 3.0 * np.array(self.yaw), gy_t], axis=1), 28.0, 0.8, dt)
        self.gaze = np.clip(gaze, [-4.2, -2.2], [4.2, 2.2])
        brow_base = np.array([GESTURES.get(self.segments[self._seg_at(ti)]["gesture"], GESTURES["talk"])["brow"] for ti in t])
        brow_t = brow_base * 0.8 + 0.7 * np.clip(beat, 0, 1) * flavor * 0.6 + 0.35 * np.array(smooth_noise(n, rng, 2.2, 1.0))
        self.brow = spring(brow_t[:, None], 14.0, 0.7, dt)[:, 0]
        self.brow_r = spring((brow_t + 0.35 * np.array(smooth_noise(n, rng, 1.7, 1.0)))[:, None], 14.0, 0.7, dt)[:, 0]
        self.brow_tilt = 5.0 * np.clip(brow_base, 0, 1) + 2.0 * np.array(smooth_noise(n, rng, 3.3, 1.0))
        # clignements
        self.blinks = []
        tb = float(rng.uniform(0.5, 1.6))
        while tb < self.duration:
            self.blinks.append(tb)
            tb += float(rng.uniform(2.0, 4.6))
        self.ph = rng.uniform(0, 6.28, 6)

    def _blink(self, t):
        for b in self.blinks:
            d = t - b
            if 0 <= d < 0.18:
                return math.sin(math.pi * d / 0.18) ** 0.8
        return 0.0

    # ------------------------------------------------------------------ pose d'une image
    def pose(self, i):
        i = min(max(i, 0), self.n - 1)
        t = i / self.fps
        ph = self.ph
        beat = float(self.beats[i])
        yaw = float(self.yaw[i])
        breath = math.sin(2 * math.pi * 0.36 * t + ph[0])
        sway = math.sin(2 * math.pi * 0.19 * t + ph[1])
        arms = self.arms[i]
        body = dict(
            dx=9.0 * yaw * 0.5 + 5.0 * sway,
            dy=-8.0 * beat * 0.6 + float(self.lift[i]) - 1.8 * breath,
            rot=1.6 * sway + 2.2 * yaw * 0.5,
            sx=1.0 - 0.045 * abs(yaw), sy=1.0 + 0.007 * breath + 0.010 * beat,
        )
        head = dict(
            dx=5.0 * yaw + 2.0 * math.sin(2 * math.pi * 0.27 * t + ph[2]),
            dy=3.6 * beat - 1.0 * breath,
            rot=float(self.tilt[i]) + 1.4 * beat * (1 if int(t * 0.9) % 2 else -1),
        )
        return dict(
            body=body, head=head, mouth=float(self.mouth[i]), wide=float(self.wide[i]), blink=self._blink(t),
            yaw=yaw, gaze=(float(self.gaze[i][0]), float(self.gaze[i][1])),
            brows=(float(self.brow[i]), float(self.brow_r[i]), float(self.brow_tilt[i])), lids=0.9 + 0.1 * math.sin(t * 0.7 + ph[3]),
            armL=list(arms[0:3]), armR=list(arms[3:6]),
        )
