"""Découpe le visage en calques mobiles : sourcils, yeux (blanc + iris), nez/traits.

Appelé par prepare_character.py. Sortie : assets/character/face_*.png + rig.json["face"].
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SKIN = np.array([236, 201, 195], np.float32)
L_SKIN = float(SKIN @ np.array([0.299, 0.587, 0.114], np.float32))
BROWS = {"L": (356, 148, 399, 176), "R": (427, 148, 478, 178)}     # x0, y0, x1, y1
EYES = {"L": (353, 176, 399, 209), "R": (422, 177, 480, 210)}
REGION = dict(c=(410, 187), rx=57, ry=46)                          # zone du visage qui « glisse » quand la tête tourne


def luma(rgb):
    return rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def dark_alpha(lum, lo=0.30):
    d = np.clip((L_SKIN - lum) / (L_SKIN - 12.0), 0, 1)
    return np.clip((d - lo) / (1 - lo), 0, 1)


def save_rgba(path, rgb, a):
    a8 = np.clip(a * 255 + 0.5, 0, 255).astype(np.uint8)
    ys, xs = np.nonzero(a8)
    if len(xs) == 0:
        raise RuntimeError(f"calque vide : {path}")
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    rgba = np.dstack([np.broadcast_to(np.asarray(rgb, np.uint8), a8.shape + (3,)), a8])[y0:y1, x0:x1]
    Image.fromarray(np.ascontiguousarray(rgba), "RGBA").save(path)
    return [int(x0), int(y0)]


def fill_holes(binary):
    h, w = binary.shape
    inv = (~binary).astype(np.uint8) * 255
    ff = inv.copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, mask, (0, 0), 0)
    return binary | (ff > 0)


def build(out_dir, head_mask):
    out_dir = Path(out_dir)
    full = np.asarray(Image.open(out_dir / "full.png").convert("RGBA"))
    rgb, alpha = full[..., :3].astype(np.float32), full[..., 3].astype(np.float32) / 255
    H, W = alpha.shape
    lum = luma(rgb)
    yy, xx = np.mgrid[0:H, 0:W]
    clean = rgb.copy()                       # tête sans sourcils ni yeux
    info = {"brows": {}, "eyes": {}, "skin": SKIN.astype(int).tolist()}

    # --- sourcils
    for side, (x0, y0, x1, y1) in BROWS.items():
        box = (xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1) & head_mask
        a = np.where(box, dark_alpha(lum), 0)
        a = np.where(a > 0.02, a, 0)
        info["brows"][side] = save_rgba(out_dir / f"face_brow{side}.png", [8, 8, 10], a)
        rm = cv2.dilate((a > 0.02).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        clean[rm & box] = SKIN

    # --- yeux : blanc (amande), iris, paupière
    for side, (x0, y0, x1, y1) in EYES.items():
        box = (xx >= x0) & (xx < x1) & (yy >= y0) & (yy < y1) & head_mask
        dk = np.where(box, dark_alpha(lum, lo=0.25), 0)
        wh = np.where(box & (rgb.min(axis=2) > 225) & (lum > L_SKIN + 6), np.clip((lum - L_SKIN - 6) / 40.0, 0, 1), 0)
        binary = ((dk > 0.3) | (wh > 0.5)) & box
        binary = cv2.morphologyEx(binary.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
        binary = fill_holes(binary)
        # on ne garde que la plus grosse forme (l'œil)
        n, lab, st, _ = cv2.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
        if n > 1:
            binary = lab == (1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA])))
        almond = cv2.GaussianBlur(binary.astype(np.float32), (0, 0), 0.7)
        inner_bin = cv2.erode(binary.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0
        inner = inner_bin.astype(np.float32)                      # coupe nette : iris + paupière = œil d'origine
        iris = dk * inner
        lid = dk * (1.0 - inner)
        info["eyes"][side] = dict(
            white=save_rgba(out_dir / f"face_eye{side}_white.png", [252, 252, 252], almond),
            inner=save_rgba(out_dir / f"face_eye{side}_inner.png", [255, 255, 255], inner),
            iris=save_rgba(out_dir / f"face_eye{side}_iris.png", [8, 8, 10], iris),
            lid=save_rgba(out_dir / f"face_eye{side}_lid.png", [8, 8, 10], lid),
        )
        rm = cv2.dilate((binary | (dk > 0.02)).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        clean[rm & box] = SKIN

    # --- zone mobile du visage (nez, joues, peau) : « feat »
    cx, cy = REGION["c"]
    ell = (((xx - cx) / REGION["rx"]) ** 2 + ((yy - cy) / REGION["ry"]) ** 2) <= 1.0
    ell &= head_mask
    cl_lum = luma(clean)
    ell &= cl_lum > 120                                # pas de cheveux, de moustache ni de barbe
    soft = cv2.GaussianBlur(ell.astype(np.float32), (0, 0), 3.0)
    soft = np.where(ell, np.minimum(1.0, soft * 1.6), 0)
    feat_rgb = clean.astype(np.uint8)
    x0, x1 = int(np.nonzero(soft.max(axis=0) > 0)[0][[0, -1]][0]), int(np.nonzero(soft.max(axis=0) > 0)[0][-1]) + 1
    y0, y1 = int(np.nonzero(soft.max(axis=1) > 0)[0][0]), int(np.nonzero(soft.max(axis=1) > 0)[0][-1]) + 1
    rgba = np.dstack([feat_rgb, np.clip(soft * 255 + 0.5, 0, 255).astype(np.uint8)])[y0:y1, x0:x1]
    Image.fromarray(np.ascontiguousarray(rgba), "RGBA").save(out_dir / "face_feat.png")
    info["feat"] = [x0, y0]

    # --- tête de base : visage vide (peau unie) sous la zone mobile
    base = clean.copy()
    fill = ell & (soft > 0.0)
    base[fill] = SKIN
    base_a = alpha * head_mask
    # plus de transition douce : sous la zone mobile la peau est unie
    bx = np.dstack([base.astype(np.uint8), np.clip(base_a * 255 + 0.5, 0, 255).astype(np.uint8)])
    ys, xs = np.nonzero(bx[..., 3] > 0)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    Image.fromarray(np.ascontiguousarray(bx[by0:by1, bx0:bx1]), "RGBA").save(out_dir / "face_base.png")
    info["base"] = [int(bx0), int(by0)]
    return info
