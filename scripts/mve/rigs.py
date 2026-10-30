# Copyright (c) 2026 Ahmii Labs
# SPDX-License-Identifier: MIT  (see LICENSE in the project root)
"""Humanoid rig, procedural motions and mesh builders for the 3D stage.

A pose is a flat dict of angles in degrees (missing key = 0):
  body   lean (torso forward), twist, tilt (side lean), pitch / roll (whole body, for flips), yaw (extra turn),
         hdp / hdy / hdr (head nod / turn / tilt), lift (metres above the floor)
  arms   aL_sw aL_rs aL_el aL_er   (L / R: shoulder swing forward, raise sideways, elbow bend, elbow raise)
  legs   lL_sw lL_sp lL_kn         (L / R: hip swing forward, spread sideways, knee bend)
The character faces +z. Its left side is +x. After solving, the lowest point of the body is put on the floor
(ground lock), so feet never float or sink - `lift` then raises the whole body for jumps.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from .geo3d import box_faces, rot_x, rot_y, rot_z, unit

THIGH = SHIN = 0.46
TORSO = 0.56
UARM = FARM = 0.30
NECK = 0.09
HEADR = 0.16
DOWN = np.array([0.0, -1.0, 0.0])

STICK_DIMS = dict(shw=0.035, hipw=0.03, foot=0.0)
BLOCK_DIMS = dict(shw=0.245, hipw=0.09, foot=0.045)


def _dir(R, sw, rs, side):
    return R @ rot_z(side * rs) @ rot_x(sw) @ DOWN


def solve(p: dict, dims: dict):
    """Pose -> (joints dict in rig space with the pelvis at the origin, y offset that puts the body on the floor)."""
    g = lambda k: float(p.get(k, 0.0))  # noqa: E731
    Rb = rot_z(g("roll")) @ rot_x(g("pitch"))
    Rt = Rb @ rot_y(g("twist")) @ rot_z(g("tilt")) @ rot_x(g("lean"))
    Rh = Rt @ rot_y(g("hdy")) @ rot_x(g("hdp")) @ rot_z(g("hdr"))
    neck = Rt @ np.array([0.0, TORSO, 0.0])
    head = neck + Rt @ np.array([0.0, NECK, 0.0]) + Rh @ np.array([0.0, HEADR, 0.0])
    J = dict(pelvis=np.zeros(3), neck=neck, head=head, Rb=Rb, Rt=Rt, Rh=Rh)
    for name, s in (("L", 1.0), ("R", -1.0)):
        sh = neck + Rt @ np.array([s * dims["shw"], -0.03, 0.0])
        el = sh + UARM * _dir(Rt, g(f"a{name}_sw"), g(f"a{name}_rs"), s)
        wr = el + FARM * _dir(Rt, g(f"a{name}_sw") + g(f"a{name}_el"), g(f"a{name}_rs") + g(f"a{name}_er"), s)
        hp = Rb @ np.array([s * dims["hipw"], 0.0, 0.0])
        kn = hp + THIGH * _dir(Rb, g(f"l{name}_sw"), g(f"l{name}_sp"), s)
        an = kn + SHIN * _dir(Rb, g(f"l{name}_sw") - g(f"l{name}_kn"), g(f"l{name}_sp"), s)
        J.update({f"sh{name}": sh, f"el{name}": el, f"wr{name}": wr, f"hp{name}": hp, f"kn{name}": kn, f"an{name}": an})
    low = min(J["anL"][1] - dims["foot"], J["anR"][1] - dims["foot"], J["wrL"][1] - 0.05, J["wrR"][1] - 0.05,
              J["knL"][1] - 0.06, J["knR"][1] - 0.06, head[1] - HEADR)
    return J, -low + g("lift")


# ---------------------------------------------------------------- procedural motions
def _smooth(a, b, x):
    if b == a:
        return 1.0 if x >= b else 0.0
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def arms(L=(0, 0, 0, 0), R=(0, 0, 0, 0)) -> dict:
    d = {}
    for n, tup in (("L", L), ("R", R)):
        for key, v in zip(("sw", "rs", "el", "er"), tup):
            d[f"a{n}_{key}"] = v
    return d


def legs(L=(0, 0, 0), R=(0, 0, 0)) -> dict:
    d = {}
    for n, tup in (("L", L), ("R", R)):
        for key, v in zip(("sw", "sp", "kn"), tup):
            d[f"l{n}_{key}"] = v
    return d


def m_idle(t, dm):
    b = math.sin(t * math.tau * 0.45)
    return dict(lean=2 + 1.2 * b, hdp=-2 * b, **arms((-3 * b, 7, 12, 0), (3 * b, 7, 12, 0)), **legs((0, 3, 2), (0, 3, 2)))


def m_walk(t, dm):
    ph = math.tau * t
    L, c = math.sin(ph), math.cos(ph)
    return dict(lean=4, twist=7 * L, tilt=2.5 * L, hdy=-4 * L,
                **arms((-28 * L, 6, 22, 0), (28 * L, 6, 22, 0)),
                **legs((32 * L, 2, 55 * max(0, c)), (-32 * L, 2, 55 * max(0, -c))))


def m_run(t, dm):
    ph = math.tau * t
    L, c = math.sin(ph), math.cos(ph)
    return dict(lean=16, twist=12 * L, hdy=-5 * L, lift=0.10 * abs(L),
                **arms((-60 * L, 8, 88, 0), (60 * L, 8, 88, 0)),
                **legs((55 * L, 3, 100 * max(0, c) + 8), (-55 * L, 3, 100 * max(0, -c) + 8)))


def m_jump(t, dm):
    u = t % 1.0
    pre = _smooth(0, 0.2, u) * (1 - _smooth(0.2, 0.28, u))
    land = (1 - _smooth(0.72, 1.0, u)) if u >= 0.72 else 0.0
    cr = max(pre, land)
    air = _smooth(0.2, 0.3, u) * (1 - _smooth(0.66, 0.74, u))
    a = max(0.0, min(1.0, (u - 0.22) / 0.5))
    lift = 1.05 * math.sin(math.pi * a) if 0 < a < 1 else 0.0
    return dict(lean=20 * cr - 4 * air, lift=lift, hdp=-8 * air,
                **arms((-35 * cr, 8 + 142 * air, 10 * air, 0), (-35 * cr, 8 + 142 * air, 10 * air, 0)),
                **legs((40 * cr + 14 * air, 4, 78 * cr + 34 * air), (40 * cr + 14 * air, 4, 78 * cr + 34 * air)))


def m_wave(t, dm):
    return dict(lean=2, hdy=8, hdr=4 * math.sin(t * math.tau * 1.2),
                **arms((0, 6, 10, 0), (0, 152, 18, 35 * math.sin(t * math.tau * 2.4))), **legs((0, 3, 2), (0, 3, 2)))


def m_dance(t, dm):
    ph = math.tau * t
    s1, s2 = math.sin(ph), math.sin(2 * ph)
    return dict(tilt=10 * s1, twist=16 * s1, lean=6 + 4 * s2, hdr=8 * s1, lift=0.03 * abs(s2),
                **arms((10, 135 + 25 * s1, 25, 0), (10, 40 + 20 * math.cos(ph), 70, 0)),
                **legs((8 * s1, 12, 35 + 25 * s2), (-8 * s1, 12, 35 - 25 * s2)))


def m_punch(t, dm):
    j = math.sin(math.tau * t * 1.5)
    pr, pl = max(0.0, j), max(0.0, -j)
    return dict(lean=10, hdp=5, twist=22 * pr - 22 * pl,
                **arms((40 + 52 * pl, 8 + 6 * pl, 100 * (1 - pl) + 4, 0), (40 + 52 * pr, 8 + 6 * pr, 100 * (1 - pr) + 4, 0)),
                **legs((-20, 8, 22), (22, 8, 28)))


def m_kick(t, dm):
    u = (t * 0.9) % 1.0
    ext = math.sin(math.pi * u) ** 1.2
    return dict(lean=-14 * ext, tilt=-5 * ext,
                **arms((0, 10 + 50 * ext, 12, 0), (0, 10 + 50 * ext, 12, 0)),
                **legs((0, 4, 12), (85 * ext, 2, 15 + 50 * (1 - ext))))


def m_flip(t, dm):
    u = (t / 1.5) % 1.0
    pre = _smooth(0, 0.16, u) * (1 - _smooth(0.16, 0.24, u))
    land = (1 - _smooth(0.78, 1.0, u)) if u >= 0.75 else 0.0
    cr = max(pre, land)
    a = max(0.0, min(1.0, (u - 0.2) / 0.55))
    air = _smooth(0.18, 0.26, u) * (1 - _smooth(0.72, 0.8, u))
    tuck = air * math.sin(math.pi * a) ** 0.7
    return dict(pitch=-360 * _smooth(0.2, 0.75, u), lift=1.3 * math.sin(math.pi * a) if 0 < a < 1 else 0.0, lean=22 * cr,
                **arms((-35 * cr + 70 * tuck, 10 + 130 * air * (1 - tuck), 100 * tuck, 0),
                       (-35 * cr + 70 * tuck, 10 + 130 * air * (1 - tuck), 100 * tuck, 0)),
                **legs((45 * cr + 90 * tuck, 4, 80 * cr + 120 * tuck), (45 * cr + 90 * tuck, 4, 80 * cr + 120 * tuck)))


def m_sit(t, dm):
    b = math.sin(t * math.tau * 0.4)
    return dict(lean=-4, hdp=3 * b, **arms((55, 8, 45, 0), (55, 8, 45, 0)), **legs((88, 6, 92), (88, 6, 92)))


def m_clap(t, dm):
    c = math.sin(math.tau * 3 * t)
    rs = -math.degrees(math.atan(dm["shw"] / 0.5)) + 7 * c
    return dict(lean=3, hdp=-4, lift=0.02 * abs(c),
                **arms((62, rs, 62, 0), (62, rs, 62, 0)), **legs((0, 5, 6), (0, 5, 6)))


def m_victory(t, dm):
    s = math.sin(math.tau * 2 * t)
    return dict(lean=-4, hdp=-8, lift=0.07 * abs(s),
                **arms((0, 145 + 10 * s, 15, 0), (0, 145 - 10 * s, 15, 0)), **legs((0, 8, 8), (0, 8, 8)))


def m_spin(t, dm):
    return dict(yaw=300 * t, **arms((0, 85, 5, 0), (0, 85, 5, 0)), **legs((0, 4, 4), (0, 4, 4)))


MOTIONS = {"idle": m_idle, "walk": m_walk, "run": m_run, "jump": m_jump, "wave": m_wave, "dance": m_dance,
           "punch": m_punch, "kick": m_kick, "flip": m_flip, "sit": m_sit, "clap": m_clap, "victory": m_victory,
           "spin": m_spin}
ALIASES = {"stand": "idle", "cheer": "victory", "backflip": "flip", "boogie": "dance", "hello": "wave", "fight": "punch"}
STRIDE = {"walk": 1.95, "run": 3.4}        # world units travelled per animation cycle (for path walking)


def motion_name(name: str | None) -> str | None:
    if not name:
        return None
    n = str(name).lower().strip()
    n = ALIASES.get(n, n)
    return n if n in MOTIONS else None


def blend_pose(a: dict, b: dict, k: float) -> dict:
    keys = set(a) | set(b)
    return {key: a.get(key, 0.0) * (1 - k) + b.get(key, 0.0) * k for key in keys}


# ---------------------------------------------------------------- placement
class Xf:
    """rig space -> world: pos + R_yaw @ (scale * (p + [0, floor_offset, 0]))"""

    def __init__(self, pos, yaw_deg: float, scale: float, off: float):
        self.pos = np.asarray(pos, float)
        self.R = rot_y(yaw_deg)
        self.s = float(scale)
        self.off = float(off)

    def pt(self, p):
        return self.pos + self.R @ (self.s * (np.asarray(p, float) + np.array([0.0, self.off, 0.0])))

    def ax(self, M):
        return self.R @ np.asarray(M, float)


# ---------------------------------------------------------------- stickman
def emit_stickman(P, J, xf: Xf, color, lw: float = 0.05, ring: bool = True, feet: bool = True) -> None:
    w = lw * xf.s
    pt = {k: xf.pt(J[k]) for k in ("pelvis", "neck", "head", "shL", "shR", "elL", "elR", "wrL", "wrR", "hpL", "hpR",
                                    "knL", "knR", "anL", "anR")}
    P.add_line(pt["pelvis"], pt["neck"], w, color)
    P.add_line(pt["neck"], xf.pt(J["neck"] + J["Rh"] @ np.array([0, NECK + 0.02, 0])), w, color)
    for n in ("L", "R"):
        P.add_line(pt["neck"], pt[f"sh{n}"], w, color)
        P.add_line(pt[f"sh{n}"], pt[f"el{n}"], w, color)
        P.add_line(pt[f"el{n}"], pt[f"wr{n}"], w, color)
        P.add_line(pt["pelvis"], pt[f"hp{n}"], w, color)
        P.add_line(pt[f"hp{n}"], pt[f"kn{n}"], w, color)
        P.add_line(pt[f"kn{n}"], pt[f"an{n}"], w, color)
        if feet:
            toe = xf.pt(J[f"an{n}"] + J["Rb"] @ np.array([0, 0, 0.13]))
            P.add_line(pt[f"an{n}"], toe, w, color)
    r = HEADR * xf.s * 1.05
    if ring:
        P.add_circle(pt["head"], r, color, ring_world=w * 0.95, shaded=False)
    else:
        P.add_circle(pt["head"], r, color, shaded=True)


# ---------------------------------------------------------------- pixelman (blocky)
def _bone(a, b, tu, tw, right, fwd):
    u = b - a
    L = float(np.linalg.norm(u))
    if L < 1e-6:
        return None
    u = u / L
    v = right - u * float(np.dot(right, u))
    if np.linalg.norm(v) < 1e-3:
        v = fwd - u * float(np.dot(fwd, u))
    v = unit(v)
    w = np.cross(u, v)
    return box_faces((a + b) / 2, [v, u, w], [tu / 2, L / 2, tw / 2])


def emit_pixelman(P, J, xf: Xf, cols: dict, outline: bool = True) -> None:
    s = xf.s
    ol = (lambda c: tuple(int(ch * 0.33) for ch in c)) if outline else (lambda c: None)  # noqa: E731
    Rb, Rt, Rh = xf.ax(J["Rb"]), xf.ax(J["Rt"]), xf.ax(J["Rh"])
    skin, hair, shirt, pants, shoes = cols["skin"], cols["hair"], cols["shirt"], cols["pants"], cols["shoes"]

    def add(faces, color, outline_color):
        v, n = faces
        P.add_faces(v, n, color, outline=outline_color)

    pt = lambda k: xf.pt(J[k])  # noqa: E731
    # torso + belt
    tr, tu_, tf = Rt[:, 0], Rt[:, 1], Rt[:, 2]
    add(box_faces((pt("pelvis") + pt("neck")) / 2, [tr, tu_, tf], [0.17 * s, TORSO / 2 * s, 0.10 * s]), shirt, ol(shirt))
    br, bu, bf = Rb[:, 0], Rb[:, 1], Rb[:, 2]
    add(box_faces(pt("pelvis") + bu * (-0.015 * s), [br, bu, bf], [0.185 * s, 0.065 * s, 0.105 * s]), pants, ol(pants))
    # arms: sleeve + skin forearm + hand
    for n in ("L", "R"):
        sh, el, wr = pt(f"sh{n}"), pt(f"el{n}"), pt(f"wr{n}")
        f = _bone(sh, el, 0.15 * s, 0.15 * s, tr, tf)
        if f:
            add(f, shirt, ol(shirt))
        f = _bone(el, wr, 0.135 * s, 0.135 * s, tr, tf)
        if f:
            add(f, skin, ol(skin))
        # legs: thigh, shin, shoe
        hp, kn, an = pt(f"hp{n}"), pt(f"kn{n}"), pt(f"an{n}")
        f = _bone(hp, kn, 0.17 * s, 0.17 * s, br, bf)
        if f:
            add(f, pants, ol(pants))
        f = _bone(kn, an, 0.16 * s, 0.16 * s, br, bf)
        if f:
            add(f, pants, ol(pants))
        add(box_faces(an + bf * (0.055 * s) + bu * (-0.0 * s), [br, bu, bf], [0.085 * s, 0.045 * s, 0.135 * s]), shoes, ol(shoes))
    # head: cube with hair + face decals
    hr, hu, hf = Rh[:, 0], Rh[:, 1], Rh[:, 2]
    hc = pt("head")
    half = 0.165 * s
    v, nrm = box_faces(hc, [hr, hu, hf], [half, half, half])
    face_cols = np.array([skin, skin, hair, skin, skin, hair], float)        # +x, -x, top, bottom, front, back
    ecol = (24, 14, 48)
    eye_c = hc + hf * (half + 0.004 * s)

    def quad(c, ax_, ay, w, h):
        return np.array([c + hr * ax_ * s + hu * ay * s + hr * w * s + hu * h * s,
                         c + hr * ax_ * s + hu * ay * s - hr * w * s + hu * h * s,
                         c + hr * ax_ * s + hu * ay * s - hr * w * s - hu * h * s,
                         c + hr * ax_ * s + hu * ay * s + hr * w * s - hu * h * s])
    decals = {4: [(quad(eye_c, 0.075, 0.02, 0.032, 0.042), ecol), (quad(eye_c, -0.075, 0.02, 0.032, 0.042), ecol),
                  (quad(eye_c, 0.0, -0.075, 0.05, 0.012), ecol),
                  (quad(eye_c, 0.0, 0.118, 0.165, 0.047), hair)]}                      # fringe
    for fi, sgn in ((0, 1), (1, -1)):                                              # hair on the sides (top strip)
        sc = hc + hr * sgn * (half + 0.004 * s)
        decals[fi] = [(np.array([sc + hu * 0.165 * s + hf * 0.165 * s, sc + hu * 0.165 * s - hf * 0.165 * s,
                                 sc + hu * 0.07 * s - hf * 0.165 * s, sc + hu * 0.07 * s + hf * 0.165 * s]), hair)]
    P.add_faces(v, nrm, face_cols, outline=ol(skin), decals=decals)


# ---------------------------------------------------------------- voxel / prop meshes (local space, base at y = 0, centred on x/z)
_AX = np.eye(3)


def _aabb(center, half, colors=None):
    v, n = box_faces(center, _AX, half)
    return v, n


def voxel_mesh(cells: dict, size: float):
    """cells: {(ix,iy,iz): (r,g,b)} -> verts (F,4,3), norms (F,3), colors (F,3) with only the exposed faces."""
    if not cells:
        return np.zeros((0, 4, 3)), np.zeros((0, 3)), np.zeros((0, 3))
    xs = [c[0] for c in cells]; ys = [c[1] for c in cells]; zs = [c[2] for c in cells]  # noqa: E702
    cx, cz, y0 = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0, min(ys)
    V, N, C = [], [], []
    for (ix, iy, iz), col in cells.items():
        centre = np.array([(ix - cx) * size, (iy - y0 + 0.5) * size, (iz - cz) * size])
        for k in range(3):
            for sgn in (1, -1):
                nb = [ix, iy, iz]
                nb[k] += sgn
                if tuple(nb) in cells:
                    continue
                n = np.zeros(3); n[k] = sgn  # noqa: E702
                a = np.zeros(3); a[(k + 1) % 3] = size / 2  # noqa: E702
                b = np.zeros(3); b[(k + 2) % 3] = size / 2  # noqa: E702
                cc = centre + n * size / 2
                V.append([cc + a + b, cc - a + b, cc - a - b, cc + a - b]); N.append(n); C.append(col)  # noqa: E702
    return np.array(V, float), np.array(N, float), np.array(C, float)


def art_cells(rows: list, palette: dict, depth: int) -> dict:
    """Pixel-art rows (top -> bottom) extruded `depth` voxels deep. '.' or ' ' = empty."""
    H = len(rows)
    cells = {}
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch in ". " or ch not in palette:
                continue
            for z in range(depth):
                cells[(c, H - 1 - r, z)] = palette[ch]
    return cells


def text_cells(fk, text: str, px: int, colors: list, depth: int) -> dict:
    """Extruded pixel text built from the bundled fonts (Latin / Urdu / Hindi all work)."""
    font = fk.get(text, px)
    tmp = ImageDraw.Draw(Image.new("L", (8, 8)))
    l_, t_, r_, b_ = tmp.multiline_textbbox((0, 0), text, font=font, align="center")
    img = Image.new("L", (int(r_ - l_ + 4), int(b_ - t_ + 4)), 0)
    ImageDraw.Draw(img).multiline_text((2 - l_, 2 - t_), text, font=font, fill=255, align="center")
    a = np.asarray(img) > 110
    H, W = a.shape
    cells = {}
    for r in range(H):
        for c in range(W):
            if a[r, c]:
                k = c / max(1, W - 1)
                if len(colors) > 1:
                    col = tuple(colors[0][i] * (1 - k) + colors[-1][i] * k for i in range(3))
                else:
                    col = colors[0]
                for z in range(depth):
                    cells[(c, H - 1 - r, z)] = col
    return cells


def cube_mesh(size, color):
    v, n = box_faces((0, size / 2, 0), _AX, (size / 2,) * 3)
    return v, n, np.tile(np.asarray(color, float), (6, 1))


def platform_mesh(w, d, h, color):
    v, n = box_faces((0, h / 2, 0), _AX, (w / 2, h / 2, d / 2))
    return v, n, np.tile(np.asarray(color, float), (6, 1))


def pyramid_mesh(size, color):
    hs = size / 2
    apex = np.array([0, size, 0.0])
    cs = [np.array(p, float) for p in ((-hs, 0, -hs), (hs, 0, -hs), (hs, 0, hs), (-hs, 0, hs))]
    V, N = [], []
    for i in range(4):
        a, b = cs[i], cs[(i + 1) % 4]
        n = unit(np.cross(b - a, apex - a))
        mid = (a + b + apex) / 3
        if np.dot(n, mid - np.array([0, size / 3, 0])) < 0:
            n = -n
        V.append([a, b, apex, apex]); N.append(n)  # noqa: E702
    V.append(cs); N.append(np.array([0, -1.0, 0]))  # noqa: E702
    return np.array(V, float), np.array(N, float), np.tile(np.asarray(color, float), (5, 1))


def tree_mesh(size, color=(48, 170, 92)):
    s = size
    parts = [((0, 0.25 * s, 0), (0.09 * s, 0.25 * s, 0.09 * s), (120, 78, 46)),
             ((0, 0.72 * s, 0), (0.42 * s, 0.22 * s, 0.42 * s), tuple(int(c * 0.85) for c in color)),
             ((0, 1.10 * s, 0), (0.32 * s, 0.17 * s, 0.32 * s), color),
             ((0, 1.40 * s, 0), (0.2 * s, 0.14 * s, 0.2 * s), tuple(min(255, int(c * 1.15)) for c in color))]
    V, N, C = [], [], []
    for c, h, col in parts:
        v, n = box_faces(c, _AX, h)
        V.append(v); N.append(n); C.append(np.tile(np.asarray(col, float), (6, 1)))  # noqa: E702
    return np.concatenate(V), np.concatenate(N), np.concatenate(C)
