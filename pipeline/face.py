"""Visage animé : tête de base + zone mobile (nez/joues) + yeux (regard, clignement) + sourcils + bouche.

Toutes les coordonnées « src » sont celles de l'image source (797x797) ; les images sont mises à l'échelle `scale`.
"""
import math

import cv2
import numpy as np
from PIL import Image

from .character_math import S, T, R, Piece


def _over(dst, src):
    """src, dst : BGRA prémultiplié, même taille."""
    dst *= (1.0 - src[..., 3:4])
    dst += src


class Face:
    def __init__(self, assets, rig, scale, head_piece):
        self.scale = scale
        self.rig = rig
        info = rig["face"]
        self.head_origin = head_piece.origin
        self.shape = head_piece.img.shape                      # (h, w, 4)

        def load(name, origin):
            return Piece(np.asarray(Image.open(assets / f"{name}.png").convert("RGBA")), origin, scale)

        self.base = load("face_base", info["base"])
        self.feat = load("face_feat", info["feat"])
        self.brow = {s: load(f"face_brow{s}", info["brows"][s]) for s in "LR"}
        self.eye = {s: {k: load(f"face_eye{s}_{k}", info["eyes"][s][k]) for k in ("white", "inner", "iris", "lid")} for s in "LR"}
        self.brow_center = {s: (info["brows"][s][0] + self.brow[s].img.shape[1] / scale / 2,
                                info["brows"][s][1] + self.brow[s].img.shape[0] / scale / 2) for s in "LR"}
        m = np.asarray(Image.open(assets / "mouth_mask.png").convert("RGBA"))[..., 3]
        self.mouth_mask = cv2.resize(m, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR) if abs(scale - 1) > 1e-3 else m
        self.mouth_origin = np.array(rig["layers"]["mouth_mask"], np.float64)
        self.skin = tuple(int(v) for v in info["skin"][::-1])  # BGR
        self.I = np.eye(3)

    # ------------------------------------------------------------------------------
    def _eye(self, side, gx, gy, blink, shift, openness):
        e = self.eye[side]
        s = self.scale
        ox, oy = e["white"].origin
        pad = 7
        wpx, hpx = e["white"].img.shape[1], e["white"].img.shape[0]
        cw, chh = wpx + int(round(2 * pad * s)), hpx + int(round(2 * pad * s))
        co = (ox - pad, oy - pad)
        cv = np.zeros((chh, cw, 4), np.float32)
        e["white"].draw(cv, self.I, co)
        iris = np.zeros_like(cv)
        e["iris"].draw(iris, T(gx, gy), co)
        inner = np.zeros_like(cv)
        e["inner"].draw(inner, self.I, co)
        iris *= inner[..., 3:4]
        _over(cv, iris)
        e["lid"].draw(cv, self.I, co)
        # paupière : « openness » < 1 abaisse la paupière du haut (regard fatigué / sceptique) ; blink = clignement
        close = min(1.0, max(blink, 1.0 - openness))
        if close > 0.02:
            top = pad * s
            edge = top + hpx * close
            yy = np.arange(chh, dtype=np.float32)[:, None]
            cover = np.clip((edge - yy) / max(1.2, 1.2 * s), 0, 1)
            cv *= (1.0 - cover[..., None])
            if close > 0.45:
                ln = np.zeros((chh, cw), np.uint8)
                x_l, x_r = pad * s + 0.12 * wpx, pad * s + 0.92 * wpx
                pts = np.array([[x_l, edge - 0.5 * s], [(x_l + x_r) / 2, edge + 1.7 * s], [x_r, edge - 0.3 * s]], np.float32)
                cv2.polylines(ln, [(pts * 16).astype(np.int32)], False, 255, max(1, int(round(1.9 * s))), cv2.LINE_AA, 4)
                a = ln.astype(np.float32) / 255.0 * min(1.0, (close - 0.4) * 8)
                lay = np.zeros_like(cv)
                lay[..., :3] = np.array([0.04, 0.03, 0.03], np.float32) * a[..., None]
                lay[..., 3] = a
                _over(cv, lay)
        return cv, co

    # ------------------------------------------------------------------------------
    def compose(self, mouth=0.0, wide=0.0, blink=0.0, yaw=0.0, gaze=(0.0, 0.0), brows=(0.0, 0.0, 0.0), lids=1.0):
        s = self.scale
        h, w = self.shape[:2]
        cv_ = np.zeros((h, w, 4), np.float32)
        co = self.head_origin
        I = self.I
        self.base.draw(cv_, I, co)
        dxf, dyf = 5.5 * yaw, 0.0
        self.feat.draw(cv_, T(dxf, dyf), co)
        # yeux (suivent le visage, un peu plus que lui)
        for side in "LR":
            cv, eco = self._eye(side, gaze[0], gaze[1], blink, None, lids)
            tmp = Piece.__new__(Piece)
            tmp.origin = np.array(eco, np.float64); tmp.scale = s; tmp.img = cv
            tmp.draw(cv_, T(dxf * 1.12 + 0.0, dyf + 0.6 * gaze[1]), co)
        # sourcils
        braise_l, braise_r, tilt = brows
        for side, br in (("L", braise_l), ("R", braise_r)):
            cx, cy = self.brow_center[side]
            sign = 1.0 if side == "L" else -1.0
            M = T(dxf * 0.4, dyf - 5.5 * br) @ R(sign * tilt, (cx, cy))
            self.brow[side].draw(cv_, M, co)
        # bouche
        if mouth > 0.05:
            self._mouth(cv_, mouth, wide, dxf)
        return cv_

    # ------------------------------------------------------------------------------
    def _mouth(self, img, m, wide, dxf):
        s = self.scale
        mo = self.rig["mouth"]
        ho = self.head_origin
        h, w = img.shape[:2]
        cx, cy = (mo["center"][0] - ho[0]) * s, (mo["center"][1] - ho[1]) * s
        rx = mo["rx"] * s * (0.70 + 0.30 * m) * (0.92 + 0.30 * wide)
        ry = max(1.0, mo["ry_max"] * s * m * (1.0 - 0.22 * wide))
        cy = cy + 1.0 * s + 0.30 * ry
        mm = np.zeros((h, w), np.float32)
        mk = self.mouth_mask
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

        def over(col, alpha):
            a = alpha[..., None].astype(np.float32)
            src = np.concatenate([np.asarray(col, np.float32) * a, a], axis=-1)
            img[...] = src + img * (1 - a)

        yy = np.arange(h, dtype=np.float32)[:, None]
        over([0.07, 0.04, 0.16], ea)                                                        # intérieur (BGR)
        if m > 0.12:                                                                        # dents du haut
            th = ry * (0.50 if wide > -0.2 else 0.25) * min(1.0, m * 2.2)
            teeth = ea * np.clip((cy - ry + 2 * th - yy) / 1.2, 0, 1)
            over([0.93, 0.94, 0.96], teeth * 0.97)
        if m > 0.5:                                                                         # langue
            tongue = ea * np.clip((yy - (cy + ry * 0.30)) / 1.5, 0, 1)
            over([0.42, 0.40, 0.84], tongue * 0.9)
