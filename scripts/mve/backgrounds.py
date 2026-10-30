# Copyright (c) 2026 Ahmii Labs
# SPDX-License-Identifier: MIT  (see LICENSE in the project root)
"""Animated backgrounds drawn over the scene's gradient (CREATE mode): aurora, grid, rays, dots, stars, waves, matrix, pixel_sky.

`bg_style` is a scene key (or top-level default). Everything is time-driven (no state), so any worker can render any frame.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .props import _hash01

STYLES = ("aurora", "grid", "rays", "dots", "stars", "waves", "matrix", "pixel_sky", "soft", "none")


def _paste(img: Image.Image, color, mask: Image.Image, strength: float = 1.0) -> None:
    if strength < 1.0:
        mask = mask.point(lambda v: int(v * strength))
    img.paste(tuple(int(c) for c in color[:3]), (0, 0), mask)


def aurora(img, t, S, accent, alt):
    W, H = img.size; bw, bh = max(16, W // 8), max(16, H // 8)
    yy, xx = np.mgrid[0:bh, 0:bw].astype(np.float32)
    acc = np.zeros((bh, bw, 3), np.float32); alpha = np.zeros((bh, bw), np.float32)
    for n, col in enumerate((accent, alt, (255, 255, 255))):
        f = np.zeros((bh, bw), np.float32)
        for j in range(2):
            cx = bw * (0.5 + 0.45 * math.sin(t * 0.22 + n * 2.1 + j * 1.9)); cy = bh * (0.5 + 0.4 * math.cos(t * 0.17 + n * 1.3 + j * 2.7))
            r = bw * (0.30 + 0.06 * n)
            f += np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
        a_ = np.clip(f, 0, 1) * (0.30, 0.26, 0.07)[n]
        acc += a_[..., None] * np.array(col[:3], np.float32); alpha += a_
    alpha = np.clip(alpha, 0, 0.9)
    color = acc / np.maximum(alpha[..., None], 1e-4)                  # one combined colour field + one mask, upscaled once
    ov = Image.fromarray(np.clip(color, 0, 255).astype(np.uint8), "RGB").resize((W, H), Image.BILINEAR)
    img.paste(ov, (0, 0), Image.fromarray((alpha * 255).astype(np.uint8), "L").resize((W, H), Image.BILINEAR))


def grid(img, t, S, accent, alt):
    """Synthwave floor: perspective grid scrolling toward the viewer."""
    W, H = img.size; hz = int(H * 0.52)
    m = Image.new("L", (W, H), 0); d = ImageDraw.Draw(m)
    for i in range(-14, 15):                                      # vertical lines converge to the horizon
        x_bot = W / 2 + i * W * 0.16
        d.line([(W / 2 + i * W * 0.012, hz), (x_bot, H)], fill=150, width=max(1, int(2 * S)))
    off = (t * 0.55) % 1.0
    for j in range(14):                                           # horizontal lines accelerate downward
        p = ((j + off) / 14.0) ** 2.2
        y = hz + (H - hz) * p
        d.line([(0, y), (W, y)], fill=int(60 + 140 * p), width=max(1, int((1 + 2 * p) * S)))
    fade = Image.linear_gradient("L").resize((W, H)).point(lambda v: min(255, int(v * 1.5)))
    from PIL import ImageChops
    m = ImageChops.multiply(m, fade)
    mask = Image.new("L", (W, H), 0); mask.paste(m.crop((0, hz, W, H)), (0, hz))
    _paste(img, accent, mask, 0.45)
    sun = Image.new("L", (W, H), 0); r = int(H * 0.2)
    ImageDraw.Draw(sun).ellipse([W / 2 - r, hz - r * 1.1, W / 2 + r, hz + r * 0.9], fill=255)
    _paste(img, alt, sun.filter(ImageFilter.GaussianBlur(r * 0.18)), 0.35)


def rays(img, t, S, accent, alt):
    W, H = img.size; w2, h2 = W // 2, H // 2
    m = Image.new("L", (w2, h2), 0); d = ImageDraw.Draw(m); cx, cy, R = w2 / 2, h2 * 0.42, math.hypot(w2, h2)
    n = 14; rot = t * 0.12
    for i in range(n):
        a0 = rot + i * math.tau / n; a1 = a0 + math.tau / n * 0.45
        d.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)), (cx + R * math.cos(a1), cy + R * math.sin(a1))], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(3)).resize((W, H), Image.BILINEAR)
    _paste(img, accent, m, 0.10)


def dots(img, t, S, accent, alt):
    W, H = img.size; w2, h2 = W // 2, H // 2; sp = max(10, int(34 * S / 2))
    m = Image.new("L", (w2, h2), 0); d = ImageDraw.Draw(m)
    for gy in range(0, h2 + sp, sp):
        for gx in range(0, w2 + sp, sp):
            r = sp * 0.42 * (0.5 + 0.5 * math.sin(gx * 0.045 + gy * 0.03 - t * 1.4))
            if r > 0.6:
                d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=255)
    _paste(img, accent, m.resize((W, H), Image.BILINEAR), 0.22)


def stars(img, t, S, accent, alt):
    W, H = img.size; m = Image.new("L", (W, H), 0); d = ImageDraw.Draw(m)
    for i in range(110):
        h = lambda k: _hash01(i * 7 + k, 3)  # noqa: E731
        x = (h(1) * W + t * 5 * S * (0.3 + h(2))) % W; y = h(3) * H; r = (0.6 + h(4) * 1.9) * S
        a = int(255 * (0.25 + 0.75 * (0.5 + 0.5 * math.sin(t * (1 + h(5) * 3) + h(6) * 9))))
        d.ellipse([x - r, y - r, x + r, y + r], fill=a)
    _paste(img, (255, 255, 255), m, 0.9)


def waves(img, t, S, accent, alt):
    W, H = img.size
    for n, (col, a) in enumerate(((accent, 0.18), (alt, 0.14), ((255, 255, 255), 0.07))):
        m = Image.new("L", (W, H), 0); d = ImageDraw.Draw(m)
        base = H * (0.70 + 0.07 * n); amp = H * (0.035 + 0.01 * n)
        pts = [(x, base + amp * math.sin(x / W * math.tau * (1.3 + 0.4 * n) + t * (0.7 + 0.25 * n) + n)) for x in range(0, W + 16, 16)]
        d.polygon(pts + [(W, H), (0, H)], fill=255); _paste(img, col, m, a)


def matrix(img, t, S, accent, alt):
    """Falling blocky 'code' columns (cyber / hacker look)."""
    W, H = img.size; cell = max(6, int(15 * S)); cols, rows = W // cell + 1, H // cell + 1
    w2, h2 = cols, rows
    m = Image.new("L", (w2, h2), 0); head = Image.new("L", (w2, h2), 0)
    dm, dh = ImageDraw.Draw(m), ImageDraw.Draw(head)
    for c in range(cols):
        h = lambda k: _hash01(c * 13 + k, 5)  # noqa: E731
        speed = 4 + h(1) * 9; tail = 6 + int(h(2) * 14); period = rows + tail + int(h(3) * 12)
        pos = (t * speed + h(4) * period) % period
        for k in range(tail):
            r = int(pos) - k
            if 0 <= r < rows:
                on = _hash01(c * 977 + r * 31 + int(t * 6 + k), 9) > 0.35
                if on:
                    dm.point((c, r), fill=int(255 * (1 - k / tail) ** 1.4))
        r0 = int(pos)
        if 0 <= r0 < rows:
            dh.point((c, r0), fill=255)
    big = Image.NEAREST
    _paste(img, alt, m.resize((W, H), big), 0.30)
    _paste(img, (255, 255, 255), head.resize((W, H), big), 0.35)


def pixel_sky(img, t, S, accent, alt):
    """Chunky pixel-art clouds drifting past twinkling pixel stars."""
    W, H = img.size; px = max(4, int(10 * S)); wc, hc = W // px + 1, H // px + 1
    stars = Image.new("L", (wc, hc), 0); clouds = Image.new("L", (wc, hc), 0)
    ds, dc = ImageDraw.Draw(stars), ImageDraw.Draw(clouds)
    for i in range(46):
        h = lambda k: _hash01(i * 7 + k, 11)  # noqa: E731
        a = int(255 * (0.3 + 0.7 * (0.5 + 0.5 * math.sin(t * (1 + h(3) * 2.5) + h(4) * 9))))
        ds.point((int(h(1) * wc), int(h(2) * hc * 0.75)), fill=a)
    for i in range(7):
        h = lambda k: _hash01(i * 11 + k, 13)  # noqa: E731
        w = 9 + int(h(1) * 9); cx = int((h(2) * (wc + 40) + t * (1.2 + h(3) * 2.2)) % (wc + 40)) - 20; cy = int(h(4) * hc * 0.55) + 2
        dc.rectangle([cx + 3, cy, cx + w - 3, cy], fill=255)
        dc.rectangle([cx, cy + 1, cx + w, cy + 2], fill=255)
        dc.rectangle([cx + 2, cy + 3, cx + w - 2, cy + 3], fill=255)
    big = Image.NEAREST
    _paste(img, (255, 255, 255), stars.resize((W, H), big), 0.9)
    _paste(img, alt, clouds.resize((W, H), big), 0.22)
    _paste(img, (255, 255, 255), clouds.resize((W, H), big), 0.10)


_FN = {"aurora": aurora, "grid": grid, "rays": rays, "dots": dots, "stars": stars, "waves": waves, "matrix": matrix, "pixel_sky": pixel_sky}


def draw(style: str, img: Image.Image, t: float, S: float, accent, alt) -> Image.Image:
    fn = _FN.get(style)
    if fn:
        fn(img, t, S, accent, alt)
    return img
