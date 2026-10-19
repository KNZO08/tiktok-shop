"""Personnage animé : calques découpés (tête, visage, mâchoire, bras…) + squelette simple.

Rig : torse (plaque) / tête (visage mobile : yeux, sourcils, bouche) / 2 bras à 3 segments
(épaule → coude → poignet).  Les angles sont donnés « vers l'extérieur » (out) :
positif = le bras s'écarte du corps / l'avant-bras se lève ; négatif = il se replie vers le corps.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .animator import FPS, GESTURE_NAMES, GESTURES, POSES, Animator  # noqa: F401  (ré-exportés)
from .character_math import R, S, T, Piece
from .face import Face

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "character"
CANVAS_SRC = (-200, -60, 930, 797)       # zone de dessin (coordonnées « image source »)


class Character:
    def __init__(self, scale=1.0):
        self.scale = scale
        rig = json.loads((ASSETS / "rig.json").read_text())
        self.rig = rig
        L = rig["layers"]

        def load(name):
            return np.asarray(Image.open(ASSETS / f"{name}.png").convert("RGBA"))

        skip = {"mouth_mask", "head"}
        self.pieces = {n: Piece(load(n), o, scale) for n, o in L.items() if n not in skip}
        head_piece = Piece(load("head"), L["head"], scale)
        self.head_piece = head_piece
        self.head_origin = np.array(L["head"], np.float64)
        self.face = Face(ASSETS, rig, scale, head_piece)
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
        # épaule arrondie : un disque apparaît sous le bras dès qu'il s'écarte (cache la coupe entre le bras et le torse)
        rs = 27
        sd = np.zeros((2 * rs + 6, 2 * rs + 6, 4), np.uint8)
        cv2.circle(sd, (rs + 3, rs + 3), rs, (*rig["sleeve_fill"], 255), -1, cv2.LINE_AA)
        self.shoulder_disc = {
            side: Piece(sd, (rig["arms"][side]["S"][0] - rs - 3, rig["arms"][side]["S"][1] - rs - 3), scale)
            for side in "LR"
        }
        # bords des bras très légèrement adoucis (évite l'effet « escalier » quand ils tournent)
        for name, pc in self.pieces.items():
            if name.startswith("arm"):
                pc.img = np.ascontiguousarray(cv2.GaussianBlur(pc.img, (0, 0), 0.5))
        x0, y0, x1, y1 = CANVAS_SRC
        self.canvas_origin = (x0, y0)
        self.size = (int(round((x1 - x0) * scale)), int(round((y1 - y0) * scale)))   # (w, h)

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
        # tête : visage composé (yeux, sourcils, bouche) puis mâchoire
        face = Piece.__new__(Piece)
        face.origin = self.head_piece.origin; face.scale = self.scale
        face.img = self.face.compose(
            mouth=pose.get("mouth", 0.0), wide=pose.get("wide", 0.0), blink=pose.get("blink", 0.0),
            yaw=pose.get("yaw", 0.0), gaze=pose.get("gaze", (0.0, 0.0)), brows=pose.get("brows", (0.0, 0.0, 0.0)),
            lids=pose.get("lids", 1.0))
        face.draw(canvas, MH, co)
        self.pieces["jaw"].draw(canvas, MH @ T(0, 2.6 * pose.get("mouth", 0.0)), co)

        for side in "LR":
            sign = -1.0 if side == "L" else 1.0
            a1, a2, a3 = pose["arm" + side]
            Sx, Ex, Wx = (rig["arms"][side][k] for k in "SEW")
            M1 = B @ R(sign * a1, Sx)
            M2 = M1 @ R(sign * a2, Ex)
            M3 = M2 @ R(sign * a3, Wx)
            f = min(1.0, max(0.0, (abs(a1) - 8.0) / 10.0))
            if f > 0.02:
                sdisc = self.shoulder_disc[side]
                tmp = Piece.__new__(Piece)
                tmp.origin, tmp.scale, tmp.img = sdisc.origin, sdisc.scale, sdisc.img * f
                tmp.draw(canvas, B, co)
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
