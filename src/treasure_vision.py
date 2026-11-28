"""Shared image helpers for treasure detection (labeling tool, dataset builder, later the detector).

Frames are full 1600x900 in-match screenshots from logs/treasure_frames/.
"""
import math

import cv2
import numpy as np

FRAME_W, FRAME_H = 1600, 900

# HUD areas of the in-match screen (x0, y0, x1, y1): nothing in them is world.
HUD_BOXES = [
    (0, 0, FRAME_W, 115),        # top bar, score bar
    (0, 620, FRAME_W, FRAME_H),  # skills, troops, progress bar
    (0, 0, 330, 340),            # Q/W portraits, pause
    (680, 150, 860, 230),        # match timer
    (1180, 0, FRAME_W, 230),     # boost cards
]


def world_mask(h=FRAME_H, w=FRAME_W):
    """uint8 mask, 1 = game world, 0 = HUD. Only meaningful for 1600x900 frames."""
    m = np.ones((h, w), np.uint8)
    if (h, w) == (FRAME_H, FRAME_W):
        for x0, y0, x1, y1 in HUD_BOXES:
            m[y0:y1, x0:x1] = 0
    return m


def glow(frame):
    """Warm light excess over the large-scale background, 0..255 float32.

    A treasure's light beam is additive warm/white light on top of the scene, so it
    stands out after subtracting a heavily blurred copy of the frame."""
    img = frame.astype(np.float32)
    small = cv2.resize(img, None, fx=0.125, fy=0.125, interpolation=cv2.INTER_AREA)
    bg = cv2.resize(cv2.blur(small, (25, 25)), (frame.shape[1], frame.shape[0]))
    e = img - bg
    b, g, r = e[:, :, 0], e[:, :, 1], e[:, :, 2]
    return np.clip((r + g) / 2 - 0.5 * np.maximum(b, 0), 0, 255)


def glow_overlay(frame, gain=4.0):
    """Frame blended with a colour map of glow(), for spotting beams by eye."""
    heat = cv2.applyColorMap(np.clip(glow(frame) * gain, 0, 255).astype(np.uint8), cv2.COLORMAP_JET)
    return cv2.addWeighted(frame, 0.55, heat, 0.45, 0)


_S = 4                     # beam search works at 1/4 scale
_BW = 9                    # band width at 1/4 scale (36 px)
_BL = 40                   # band length at 1/4 scale (160 px)


def _band_kernel(angle, inner):
    n = _BL + 2 * _BW
    k = np.zeros((n, n), np.float32)
    c = n / 2
    dx, dy = math.cos(math.radians(angle)), -math.sin(math.radians(angle))
    nx, ny = -dy, dx
    for sgn in ([0] if inner else [-1, 1]):
        ox, oy = sgn * nx * _BW, sgn * ny * _BW
        p1 = (int(c - dx * _BL / 2 + ox), int(c - dy * _BL / 2 + oy))
        p2 = (int(c + dx * _BL / 2 + ox), int(c + dy * _BL / 2 + oy))
        cv2.line(k, p1, p2, 1.0, _BW if inner else max(_BW // 2, 1))
    return k / k.sum()


_BEAM_KERNELS = [(a, _band_kernel(a, True), _band_kernel(a, False)) for a in range(60, 121, 5)]


def beam_candidates(frame, min_score=20.0, min_len=140, max_top=140):
    """Light-beam candidates: a long band of warm light, brighter than both of its sides,
    that reaches the top of the world area. Returns [{"foot": (x, y), "score", "len"}],
    foot = lower end of the band (where the treasure lies). Low precision on busy
    battle frames (spells, ice, explosions): use as labeling hints, not as a detector."""
    gl = glow(frame) * world_mask(*frame.shape[:2])
    g = cv2.resize(gl, None, fx=1 / _S, fy=1 / _S, interpolation=cv2.INTER_AREA)
    best = np.zeros_like(g)
    for _, ki, ko in _BEAM_KERNELS:
        np.maximum(best, cv2.filter2D(g, -1, ki) - cv2.filter2D(g, -1, ko), out=best)
    n, lab, st, _ = cv2.connectedComponentsWithStats((best > min_score).astype(np.uint8))
    out = []
    for i in range(1, n):
        if st[i][1] * _S > max_top:
            continue
        ys, xs = np.where(lab == i)
        length = math.hypot(np.ptp(xs), np.ptp(ys)) * _S
        if length < min_len:
            continue
        j = ys.argmax()
        out.append({"foot": (int(xs[j] * _S), int(ys[j] * _S)),
                    "score": float(best[lab == i].max()), "len": int(length)})
    return out


def crop(frame, x, y, size):
    """size x size crop centred on (x, y); padded with black at the frame edge."""
    h = size // 2
    pad = cv2.copyMakeBorder(frame, h, h, h, h, cv2.BORDER_CONSTANT, value=0)
    return pad[y:y + size, x:x + size].copy()
