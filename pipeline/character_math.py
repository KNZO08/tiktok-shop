"""Maths 2D et calques (pièces) utilisés par le personnage."""
import math

import cv2
import numpy as np


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
