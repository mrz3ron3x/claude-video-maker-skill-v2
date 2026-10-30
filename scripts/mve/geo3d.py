# Copyright (c) 2026 Ahmii Labs
# SPDX-License-Identifier: MIT  (see LICENSE in the project root)
"""A tiny software 3D renderer for the motion engine (numpy + Pillow only - no GPU, no extra installs).

World axes: +x = right, +y = up, +z = towards the viewer when the camera yaw is 0. Characters face +z by default.
Everything is flat-shaded faces drawn with the painter's algorithm (far -> near), plus screen-space lines and balls for
neon stickmen. It is deterministic (any worker can render any frame) and fast enough for a weak PC.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import ImageDraw

LIGHT = np.array([-0.45, 0.85, 0.55])
LIGHT = LIGHT / np.linalg.norm(LIGHT)
NEAR = 0.15


# ---------------------------------------------------------------- small math
def rot_x(a: float) -> np.ndarray:
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], float)


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)


def rot_z(a: float) -> np.ndarray:
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


def unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


# ---------------------------------------------------------------- camera
class Camera:
    """Orbit camera looking at `target`. yaw 0 = in front of the scene (+z), positive yaw moves the camera to +x."""

    def __init__(self, W: int, H: int, yaw=0.0, pitch=12.0, dist=6.5, fov=38.0, target=(0, 1, 0), ortho=False, roll=0.0):
        self.W, self.H, self.ortho = W, H, bool(ortho)
        self.target = np.asarray(target, float)
        pitch = max(-85.0, min(85.0, float(pitch)))
        dist = max(0.3, float(dist))
        fov = max(5.0, min(120.0, float(fov)))
        y, p = math.radians(yaw), math.radians(pitch)
        self.pos = self.target + dist * np.array([math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p)])
        f = unit(self.target - self.pos)
        r = unit(np.cross(f, [0.0, 1.0, 0.0]))
        u = np.cross(r, f)
        if roll:
            c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
            r, u = r * c + u * s, u * c - r * s
        self.R = np.stack([r, u, f])                       # rows: right, up, forward
        t = math.tan(math.radians(fov) / 2)
        self.focal = (H / 2) / t                           # pixels, perspective
        self.oscale = (H / 2) / (dist * t)                 # pixels per unit, orthographic

    def to_view(self, P: np.ndarray) -> np.ndarray:
        return (np.asarray(P, float) - self.pos) @ self.R.T

    def project(self, P: np.ndarray):
        """-> (N,2) screen points in this camera's pixel space, (N,) depth along the view direction."""
        v = self.to_view(np.atleast_2d(P))
        z = v[:, 2]
        if self.ortho:
            sx, sy = self.W / 2 + self.oscale * v[:, 0], self.H / 2 - self.oscale * v[:, 1]
        else:
            zz = np.maximum(z, 1e-3)
            sx, sy = self.W / 2 + self.focal * v[:, 0] / zz, self.H / 2 - self.focal * v[:, 1] / zz
        return np.stack([sx, sy], 1), z

    def px_per_unit(self, z: float) -> float:
        return self.oscale if self.ortho else self.focal / max(z, 1e-3)


# ---------------------------------------------------------------- boxes
def box_faces(center, axes, half):
    """6 quads of an oriented box. axes: 3 unit vectors (rows) u,v,w; half: half sizes. -> verts (6,4,3), normals (6,3)."""
    c = np.asarray(center, float)
    axes = np.asarray(axes, float)
    half = np.asarray(half, float)
    verts, norms = [], []
    for k in range(3):
        for s in (1.0, -1.0):
            n = axes[k] * s
            a = axes[(k + 1) % 3] * half[(k + 1) % 3]
            b = axes[(k + 2) % 3] * half[(k + 2) % 3]
            cc = c + n * half[k]
            verts.append([cc + a + b, cc - a + b, cc - a - b, cc + a - b])
            norms.append(n)
    return np.array(verts), np.array(norms)


# ---------------------------------------------------------------- painter
class Painter:
    """Collects faces, lines and balls, then draws them far -> near onto a PIL ImageDraw (RGBA image)."""

    def __init__(self, cam: Camera):
        self.cam = cam
        self.V, self.N, self.C, self.OL, self.SH, self.DR = [], [], [], [], [], []
        self.decal_lists: list = []
        self.items: list = []
        self._poly_width_ok = True

    # ---- submit geometry
    def add_faces(self, verts, norms, colors, outline=None, shade=True, decals=None) -> None:
        verts = np.asarray(verts, float)
        n = len(verts)
        if n == 0:
            return
        self.V.append(verts)
        self.N.append(np.asarray(norms, float))
        self.C.append(np.broadcast_to(np.asarray(colors, float), (n, 3)).copy())
        self.OL.append(np.tile(np.asarray(outline, float), (n, 1)) if outline is not None else np.full((n, 3), -1.0))
        self.SH.append(np.full(n, bool(shade)))
        ref = np.full(n, -1, int)
        for fi, lst in (decals or {}).items():
            ref[fi] = len(self.decal_lists)
            self.decal_lists.append(lst)
        self.DR.append(ref)

    def add_line(self, a, b, width_world: float, color) -> None:
        cam = self.cam
        pts, z = cam.project(np.array([a, b], float))
        if z.min() < NEAR and not cam.ortho:
            return
        zm = float(z.mean())
        self.items.append((zm, 1, "l", (pts[0], pts[1], width_world * cam.px_per_unit(zm), tuple(color))))

    def add_circle(self, c, radius_world: float, color, ring_world=None, shaded=True) -> None:
        cam = self.cam
        pts, z = cam.project(np.array([c], float))
        if z[0] < NEAR and not cam.ortho:
            return
        ppu = cam.px_per_unit(float(z[0]))
        self.items.append((float(z[0]), 1, "c", (pts[0], radius_world * ppu, tuple(color),
                                                  None if ring_world is None else ring_world * ppu, shaded)))

    # ---- floor helpers (drawn before the faces)
    def _seg_ok(self, z0, z1):
        return self.cam.ortho or (z0 > NEAR and z1 > NEAR)

    def floor_grid(self, dr, k: float, ext: float, step: float, color, alpha: float, width_px: float, cx=0.0, cz=0.0) -> None:
        cam = self.cam
        n = int(ext / step)
        sub = 12
        ts = np.linspace(-ext, ext, sub + 1)
        segs = []
        for i in range(-n, n + 1):
            c = i * step
            for j in range(sub):
                segs.append(((c, 0.0, ts[j]), (c, 0.0, ts[j + 1]), i))
                segs.append(((ts[j], 0.0, c), (ts[j + 1], 0.0, c), i))
        P = np.array([[s[0], s[1]] for s in segs], float)              # (M,2,3)
        pts, z = cam.project(P.reshape(-1, 3))
        pts = pts.reshape(-1, 2, 2) * k
        z = z.reshape(-1, 2)
        mid = P.mean(1)
        dist = np.hypot(mid[:, 0] - cx, mid[:, 2] - cz)
        fade = np.clip(1 - dist / ext, 0, 1) ** 1.25
        for (p0, p1), zz, f, s in zip(pts, z, fade, segs):
            if f <= 0.02 or not self._seg_ok(zz[0], zz[1]):
                continue
            major = (s[2] % 5 == 0)
            a = alpha * f * (1.0 if major else 0.7)
            dr.line([tuple(p0), tuple(p1)], fill=(*[int(c) for c in color], int(255 * min(1.0, a))),
                    width=max(1, int(round(width_px * (1.5 if major else 1.0)))))

    def floor_checker(self, dr, k: float, ext: float, step: float, c1, c2, alpha: float, cx=0.0, cz=0.0) -> None:
        cam = self.cam
        n = int(ext / step)
        quads = []
        for i in range(-n, n):
            for j in range(-n, n):
                x0, z0 = i * step, j * step
                quads.append(([(x0, 0, z0), (x0 + step, 0, z0), (x0 + step, 0, z0 + step), (x0, 0, z0 + step)], (i + j) % 2))
        P = np.array([q[0] for q in quads], float)
        pts, z = cam.project(P.reshape(-1, 3))
        pts = pts.reshape(-1, 4, 2) * k
        z = z.reshape(-1, 4)
        ctr = P.mean(1)
        fade = np.clip(1 - np.hypot(ctr[:, 0] - cx, ctr[:, 2] - cz) / ext, 0, 1) ** 1.1
        for q, zz, f, (_, par) in zip(pts, z, fade, quads):
            if f <= 0.02 or (not cam.ortho and zz.min() < NEAR):
                continue
            col = c1 if par == 0 else c2
            dr.polygon([tuple(p) for p in q], fill=(*[int(c) for c in col], int(255 * alpha * f)))

    def floor_solid(self, dr, k: float, ext: float, color, alpha: float) -> None:
        self.floor_checker(dr, k, ext, ext / 4, color, color, alpha)

    def shadow(self, dr, k: float, x: float, z: float, radius: float, alpha: float = 0.42) -> None:
        cam = self.cam
        th = np.linspace(0, math.tau, 28, endpoint=False)
        P = np.stack([x + radius * np.cos(th), np.full_like(th, 0.003), z + radius * np.sin(th) * 0.9], 1)
        pts, zz = cam.project(P)
        if not cam.ortho and zz.min() < NEAR:
            return
        dr.polygon([tuple(p * k) for p in pts], fill=(0, 0, 0, int(255 * alpha)))

    # ---- draw everything submitted
    def draw(self, dr: ImageDraw.ImageDraw, k: float = 1.0, ambient: float = 0.52, outline_px: float = 1.0) -> None:
        cam = self.cam
        prim = list(self.items)
        if self.V:
            V = np.concatenate(self.V)
            N = np.concatenate(self.N)
            C = np.concatenate(self.C)
            OL = np.concatenate(self.OL)
            SH = np.concatenate(self.SH)
            DR = np.concatenate(self.DR)
            ctr = V.mean(1)
            if cam.ortho:
                facing = (N @ cam.R[2]) < -1e-6
            else:
                facing = ((cam.pos - ctr) * N).sum(1) > 1e-6
            idx = np.nonzero(facing)[0]
            if len(idx):
                pts, z = cam.project(V[idx].reshape(-1, 3))
                pts = pts.reshape(-1, 4, 2) * k
                z = z.reshape(-1, 4)
                keep = np.ones(len(idx), bool) if cam.ortho else (z.min(1) > NEAR)
                lam = np.clip(N[idx] @ LIGHT, 0, 1)
                shade = np.where(SH[idx], ambient + (1 - ambient) * lam, 1.0)
                col = np.clip(C[idx] * shade[:, None], 0, 255)
                zc = z.mean(1)
                for j in np.nonzero(keep)[0]:
                    prim.append((float(zc[j]), 0, "f", (pts[j], col[j], OL[idx[j]], int(DR[idx[j]]))))
        prim.sort(key=lambda p: (-p[0], p[1]))
        ow = max(1, int(round(outline_px * k)))
        for _, _, kind, a in prim:
            if kind == "f":
                pts, col, ol, dref = a
                poly = [tuple(p) for p in pts.tolist()]
                fill = (int(col[0]), int(col[1]), int(col[2]), 255)
                if ol[0] >= 0:
                    oc = (int(ol[0]), int(ol[1]), int(ol[2]), 255)
                    self._poly(dr, poly, fill, oc, ow)
                else:
                    dr.polygon(poly, fill=fill)
                if dref >= 0:
                    for dv, dc in self.decal_lists[dref]:
                        dp, dz = cam.project(np.asarray(dv, float))
                        dr.polygon([tuple(p * k) for p in dp], fill=(int(dc[0]), int(dc[1]), int(dc[2]), 255))
            elif kind == "l":
                p0, p1, w, col = a
                w = max(1.0, w * k)
                x0, y0, x1, y1 = p0[0] * k, p0[1] * k, p1[0] * k, p1[1] * k
                c = (int(col[0]), int(col[1]), int(col[2]), 255)
                dr.line([(x0, y0), (x1, y1)], fill=c, width=int(round(w)))
                if w >= 3:
                    r = w / 2
                    for (x, y) in ((x0, y0), (x1, y1)):
                        dr.ellipse([x - r, y - r, x + r, y + r], fill=c)
            else:  # circle
                ctr_, r, col, ring, shaded = a
                x, y, r = ctr_[0] * k, ctr_[1] * k, max(1.0, r * k)
                if ring is not None:
                    c = (int(col[0]), int(col[1]), int(col[2]), 255)
                    dr.ellipse([x - r, y - r, x + r, y + r], outline=c, width=max(1, int(round(ring * k))))
                elif shaded:
                    for sc, off, f in ((1.0, 0.0, 0.62), (0.84, 0.13, 0.85), (0.5, 0.28, 1.12)):
                        rr = r * sc
                        ox, oy = -off * r * 0.8, -off * r
                        c = tuple(int(min(255, ch * f)) for ch in col) + (255,)
                        dr.ellipse([x + ox - rr, y + oy - rr, x + ox + rr, y + oy + rr], fill=c)
                else:
                    c = (int(col[0]), int(col[1]), int(col[2]), 255)
                    dr.ellipse([x - r, y - r, x + r, y + r], fill=c)

    def _poly(self, dr, poly, fill, outline, ow) -> None:
        if self._poly_width_ok:
            try:
                dr.polygon(poly, fill=fill, outline=outline, width=ow)
                return
            except TypeError:                        # very old Pillow: no `width` on polygons
                self._poly_width_ok = False
        dr.polygon(poly, fill=fill, outline=outline)
