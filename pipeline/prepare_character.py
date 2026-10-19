"""Découpe le personnage (capture d'écran Adobe Express, fond vert) en calques animables.

À lancer une seule fois :  .venv/bin/python -m pipeline.prepare_character
Sortie : assets/character/*.png + assets/character/rig.json
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "screenshot_source.webp"
OUT = ROOT / "assets" / "character"
CROP = (111, 213, 908, 1010)  # zone verte dans la capture (x0, y0, x1, y1)

# ---- rig (coordonnées dans l'image recadrée 797x797) --------------------------------
RIG = {
    "size": [797, 797],
    "body_pivot": [440, 790],
    "head_pivot": [440, 300],
    "mouth": {"center": [410, 259], "rx": 17, "ry_max": 13},
    "eyes": [
        {"center": [377, 192], "rx": 21, "ry": 13},
        {"center": [450, 192], "rx": 24, "ry": 13},
    ],
    "arms": {
        # S = épaule, E = coude, W = poignet
        "L": {"S": [366, 368], "E": [326, 508], "W": [249, 654]},
        "R": {"S": [535, 368], "E": [565, 510], "W": [590, 682]},
    },
    "elbow_radius": 28,
}

HEAD_POLY = [(330, 30), (560, 30), (560, 298), (502, 306), (478, 303), (463, 312), (440, 320),
             (420, 321), (397, 319), (378, 309), (362, 302), (330, 302)]
ARM_POLY = {
    "L": [(362, 330), (356, 525), (350, 548), (345, 562), (336, 600), (322, 640), (318, 690), (318, 800), (120, 800), (120, 330)],
    "R": [(528, 330), (533, 522), (535, 548), (537, 560), (544, 600), (551, 690), (553, 800), (880, 800), (880, 330)],
}
HAND_MIN_Y = {"L": 632, "R": 668}
JAW_TOP = 277
JAW_X = (352, 495)


def poly_mask(shape, pts):
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
    return m > 0


def key_green(rgb):
    """Retire le fond vert et reconstitue les bords (unmix)."""
    f = rgb.astype(np.float32)
    bg = np.median(np.concatenate([f[:6].reshape(-1, 3), f[:, :6].reshape(-1, 3)]), axis=0)
    g = f[..., 1] - np.maximum(f[..., 0], f[..., 2])
    g_bg = float(bg[1] - max(bg[0], bg[2]))
    a = np.clip(1.0 - g / g_bg, 0, 1)
    a[a < 0.05] = 0
    a[a > 0.97] = 1
    # on garde la plus grosse forme (le personnage)
    n, lab, stats, _ = cv2.connectedComponentsWithStats((a > 0.05).astype(np.uint8), connectivity=8)
    keep = np.zeros_like(a, bool)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] > 400:
            keep |= lab == i
    a = np.where(keep, a, 0)
    a[:3, :] = 0; a[-3:, :] = 0; a[:, :3] = 0; a[:, -3:] = 0   # liseré de la capture
    a3 = np.maximum(a, 1e-3)[..., None]
    fg = (f - (1 - a[..., None]) * bg) / a3
    fg = np.clip(fg, 0, 255)
    # dé-spill : plus aucun vert résiduel
    fg[..., 1] = np.minimum(fg[..., 1], np.maximum(fg[..., 0], fg[..., 2]) + 6)
    return fg.astype(np.uint8), (a * 255).astype(np.uint8)


def save_layer(name, rgb, alpha, mask=None, pad=2):
    """Enregistre un calque rogné ; renvoie (x, y) du coin haut-gauche."""
    if mask is not None:
        alpha = np.where(mask, alpha, 0).astype(np.uint8)
    # on retire les poussières (petits îlots isolés)
    n, lab, stats, _ = cv2.connectedComponentsWithStats((alpha > 0).astype(np.uint8), connectivity=8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 60:
            alpha[lab == i] = 0
    ys, xs = np.nonzero(alpha)
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, alpha.shape[1])
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, alpha.shape[0])
    rgba = np.dstack([rgb, alpha])[y0:y1, x0:x1]
    Image.fromarray(rgba, "RGBA").save(OUT / f"{name}.png")
    return [int(x0), int(y0)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    im = Image.open(SRC).convert("RGB").crop(CROP)
    rgb, alpha = key_green(np.asarray(im))
    H, W = alpha.shape
    solid = alpha > 12
    Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(OUT / "full.png")

    yy, xx = np.mgrid[0:H, 0:W]
    luma = rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)

    head = poly_mask(alpha.shape, HEAD_POLY) & solid
    arms = {}
    for side in "LR":
        arms[side] = poly_mask(alpha.shape, ARM_POLY[side]) & solid & ~head

    layers = {}
    rig = json.loads(json.dumps(RIG))
    arms_plate = {k: v.copy() for k, v in arms.items()}          # coupe nette pour la plaque
    for side, dx in (("L", 6), ("R", -6)):
        poly = ARM_POLY[side]
        strip = [poly[0], (poly[0][0] + dx, poly[0][1]), (poly[1][0] + dx, poly[1][1]), (poly[2][0] + dx, poly[2][1]),
                 poly[2], poly[1]]
        arms[side] = arms[side] | (poly_mask(alpha.shape, strip) & solid & ~head)

    # ---- bras : épaule / avant-bras / main ----------------------------------------
    r = RIG["elbow_radius"]
    for side in "LR":
        S, E, Wp = (np.array(RIG["arms"][side][k], np.float32) for k in "SEW")
        u = (E - S) / np.linalg.norm(E - S)
        dot = (xx - E[0]) * u[0] + (yy - E[1]) * u[1]
        disc = (xx - E[0]) ** 2 + (yy - E[1]) ** 2 <= r * r
        arm = arms[side]
        # manche = zone sombre connectée au coude ; main = le reste du bras
        dark = (arm & (luma < 100) & (yy < Wp[1] + 28)).astype(np.uint8)
        n, lab = cv2.connectedComponents(dark, connectivity=8)
        sleeve = lab == lab[int(E[1]), int(E[0])]
        sleeve = cv2.dilate(sleeve.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        sleeve &= arm
        hand = arm & ~sleeve & (yy >= HAND_MIN_Y[side])
        sleeve = arm & ~hand
        upper = sleeve & (dot <= 0)
        fore = sleeve & ((dot > 0) | disc)
        for nm, m in (("upper", upper), ("fore", fore), ("hand", hand)):
            layers[f"arm{side}_{nm}"] = save_layer(f"arm{side}_{nm}", rgb, alpha, m)

    # ---- tête / mâchoire ------------------------------------------------------------
    layers["head"] = save_layer("head", rgb, alpha, head)
    jaw = head & (yy >= JAW_TOP) & (xx >= JAW_X[0]) & (xx <= JAW_X[1])
    layers["jaw"] = save_layer("jaw", rgb, alpha, jaw)

    # zone de la bouche : peau entre moustache et barbe
    seed = tuple(RIG["mouth"]["center"])
    box = np.zeros(alpha.shape, bool)
    box[238:283, 358:464] = True
    skin = (rgb[..., 0] > 170) & (rgb[..., 1] > 130) & (luma > 140) & box & head
    n, lab = cv2.connectedComponents(skin.astype(np.uint8), connectivity=4)
    mouth_mask = lab == lab[seed[1], seed[0]] if lab[seed[1], seed[0]] > 0 else skin
    mouth_mask = cv2.morphologyEx(mouth_mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
    layers["mouth_mask"] = save_layer("mouth_mask", np.full_like(rgb, 255), np.full_like(alpha, 255), mouth_mask, pad=0)

    # couleur de peau (paupières) : médiane autour des yeux
    skin_tone = []
    for e in RIG["eyes"]:
        cx, cy = e["center"]
        ring = (np.abs(xx - cx) < e["rx"] + 12) & (np.abs(yy - cy) < e["ry"] + 14) & ~((np.abs(xx - cx) < e["rx"]) & (np.abs(yy - cy) < e["ry"]))
        sel = ring & head & (rgb[..., 0] > 200) & (rgb[..., 1] > 160)
        skin_tone.append(np.median(rgb[sel], axis=0).astype(int).tolist())
    rig["skin_tones"] = skin_tone

    # ---- plaque (torse, chemise) : tout sauf tête et bras ----------------------------
    plate = solid & ~head
    for side in "LR":
        plate &= ~arms_plate[side]
    plate_alpha = np.where(plate, alpha, 0).astype(np.uint8)
    plate_rgb = rgb.copy()

    # cou : visible si la tête monte, caché sinon
    neck = poly_mask(alpha.shape, [(404, 280), (472, 280), (476, 336), (398, 336)]) & head
    neck_col = np.array([222, 183, 176], np.uint8)
    plate_rgb[neck] = neck_col
    plate_alpha[neck] = 255
    # épaules du torse : contour noir le long de la coupe pour que le bras puisse s'écarter
    pa = plate_alpha.copy()
    prgb = plate_rgb.copy()
    for side, pts in (("L", [(364, 344), (358, 508)]), ("R", [(526, 344), (531, 508)])):
        cv2.polylines(prgb, [np.array(pts, np.int32)], False, (14, 14, 14), 5, cv2.LINE_AA)
        # le trait ne doit pas déborder hors du torse
        edge = np.zeros(alpha.shape, np.uint8)
        cv2.polylines(edge, [np.array(pts, np.int32)], False, 255, 5, cv2.LINE_AA)
        pa = np.maximum(pa, (edge * (plate_alpha > 0)).astype(np.uint8))
    layers["plate"] = save_layer("plate", prgb, pa)

    # cercles de coude (sous les pièces) : couleur de manche + contour
    ys_, xs_ = np.nonzero(solid)
    rig["rest_bbox"] = [int(xs_.min()), int(ys_.min()), int(xs_.max()) + 1, int(ys_.max()) + 1]
    from . import prepare_face
    rig["face"] = prepare_face.build(OUT, head)
    rig["layers"] = layers
    rig["sleeve_fill"] = [43, 43, 43]
    rig["sleeve_line"] = [10, 10, 10]
    (OUT / "rig.json").write_text(json.dumps(rig, indent=1))

    # aperçu de contrôle des masques
    dbg = np.zeros((H, W, 3), np.uint8)
    dbg[:] = (60, 60, 60)
    cols = {"head": (255, 200, 0), "jaw": (255, 0, 0), "L": (0, 255, 255), "R": (255, 0, 255)}
    dbg[plate] = (120, 120, 120)
    dbg[head] = cols["head"]
    dbg[jaw] = cols["jaw"]
    for side in "LR":
        dbg[arms[side]] = cols[side]
        S, E, Wp = (RIG["arms"][side][k] for k in "SEW")
        for p, c in ((S, (0, 255, 0)), (E, (255, 255, 0)), (Wp, (0, 0, 255))):
            cv2.circle(dbg, tuple(p), 6, c, -1)
    dbg[mouth_mask] = (0, 255, 0)
    for e in RIG["eyes"]:
        cv2.ellipse(dbg, tuple(e["center"]), (e["rx"], e["ry"]), 0, 0, 360, (255, 255, 255), 1)
    Image.fromarray(dbg).save(ROOT / "work" / "debug_masks.png")
    make_preview()
    print("OK", json.dumps(layers), rig["rest_bbox"])


def make_preview():
    """Image du personnage au repos pour l'aperçu des réglages."""
    from .character import CANVAS_SRC, POSES, Character
    ch = Character(1.0)
    g = POSES["hang"]
    pose = dict(body=dict(dx=0, dy=0, rot=0, sx=1, sy=1), head=dict(dx=0, dy=0, rot=0), mouth=0.0, blink=0.0,
                armL=list(g["L"]), armR=list(g["R"]), fade_rows=60)
    img = ch.render(pose)
    bx0, by0, bx1, by1 = json.loads((OUT / "rig.json").read_text())["rest_bbox"]
    crop = img[by0 - CANVAS_SRC[1]: by1 - CANVAS_SRC[1], bx0 - CANVAS_SRC[0]: bx1 - CANVAS_SRC[0]]
    rgba = crop[..., [2, 1, 0, 3]]
    (ROOT / "web").mkdir(exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(ROOT / "web" / "char_preview.png")


if __name__ == "__main__":
    main()
