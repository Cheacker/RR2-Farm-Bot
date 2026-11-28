"""Shared image helpers for treasure detection (labeling tool, dataset builder, later the detector).

Frames are full 1600x900 in-match screenshots from logs/treasure_frames/.
"""
import cv2
import numpy as np

FRAME_W, FRAME_H = 1600, 900

# HUD areas of the in-match screen (x0, y0, x1, y1): nothing in them is world.
HUD_BOXES = [
    (0, 0, FRAME_W, 115),        # top bar, score bar
    (0, 620, FRAME_W, FRAME_H),  # skills, troops, progress bar
    (0, 0, 230, 340),            # hero portrait, pause
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


def crop(frame, x, y, size):
    """size x size crop centred on (x, y); padded with black at the frame edge."""
    h = size // 2
    pad = cv2.copyMakeBorder(frame, h, h, h, h, cv2.BORDER_CONSTANT, value=0)
    return pad[y:y + size, x:x + size].copy()
