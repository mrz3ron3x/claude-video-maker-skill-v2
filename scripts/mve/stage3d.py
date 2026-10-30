# Copyright (c) 2026 Ahmii Labs
# SPDX-License-Identifier: MIT  (see LICENSE in the project root)
"""The 3D stage: stickmen, pixel-men, voxel props and extruded text, rendered by the software rasteriser in geo3d.

A `stage3d` layer (or a `world3d` scene) holds a camera `view`, a `floor` and a list of `actors`:

  view     "front" | "three_quarter" | "side" | "top" | "iso" | "orbit" | "push_in" | "chase"
           or {"preset": "orbit", "yaw": .., "pitch": .., "dist": .., "fov": .., "target": [x,y,z], "orbit": deg/s,
               "ortho": true, "follow": "actor_id", "roll": ..}   (every number may be keyframed / an expression)
  floor    "grid" | "checker" | "solid" | "none"          floor_color, floor_ext, floor_step
  pixel    0 (smooth, default) or 3..8 = chunky pixel-art look (renders small, scales up with hard pixels)
  bloom    0..1  neon glow around everything             ss  supersampling override

  actor    kind: stickman | pixelman | cube | sphere | pyramid | tree | platform | voxels | voxel_text
           x, y, z, yaw, scale (all animatable), color, colors {skin,hair,shirt,pants,shoes}, start, enter (pop|drop|spin_in|rise)
           motion "walk" or actions [{"motion": "walk", "dur": 2}, {"motion": "wave"}], speed
           path [[x,z], ...] + walk_speed  (walks the route, faces the way it goes, animation synced to distance)
           say [{"text": "Hi!", "start": 1, "dur": 2}]  speech bubbles
           props: size, spin (deg/s), bob;  voxels: art [rows], palette {char: colour}, depth;  voxel_text: text, size, depth
"""
from __future__ import annotations

import difflib
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import rigs as R
from .compositing import place
from .easing import ease
from .geo3d import Camera, Painter, rot_y
from .layers import Layer, color_ok, parse_color
from .props import Prop, check as check_prop

KINDS = ("stickman", "pixelman", "cube", "sphere", "pyramid", "tree", "platform", "voxels", "voxel_text")
FLOORS = ("grid", "checker", "solid", "none")
ENTERS = ("pop", "drop", "spin_in", "rise")
VIEW_PRESETS = {
    "front": dict(yaw=0, pitch=8, dist=6.5, fov=38),
    "three_quarter": dict(yaw=28, pitch=14, dist=6.5, fov=38),
    "side": dict(yaw=90, pitch=8, dist=6.5, fov=38),
    "top": dict(yaw=0, pitch=78, dist=8.0, fov=38),
    "iso": dict(yaw=45, pitch=35, dist=11.0, fov=38, ortho=True),
    "orbit": dict(yaw="22 + t*30", pitch=14, dist=6.5, fov=38),
    "push_in": dict(yaw=14, pitch=10, dist="8.2 - 3.2*smooth(0, d, t)", fov=38),
    "chase": dict(yaw=-20, pitch=12, dist=4.6, fov=42),
}
VIEWS = tuple(VIEW_PRESETS)
STAGE_KEYS = ("view", "floor", "floor_color", "floor_ext", "floor_step", "actors", "pixel", "bloom", "ss")
DARK = (22, 12, 44)


def _p(spec, key, default):
    return Prop(spec.get(key), default, key)


# ======================================================================= camera
class CameraRig:
    def __init__(self, spec):
        if spec is None:
            spec = "three_quarter"
        if isinstance(spec, str):
            spec = {"preset": spec}
        self.preset = spec.get("preset", "three_quarter")
        base = dict(VIEW_PRESETS.get(self.preset, VIEW_PRESETS["three_quarter"]))
        base.update({k: v for k, v in spec.items() if k != "preset"})
        self.yaw, self.pitch, self.dist, self.fov = _p(base, "yaw", 0), _p(base, "pitch", 10), _p(base, "dist", 6.5), _p(base, "fov", 38)
        self.roll = _p(base, "roll", 0)
        tg = base.get("target", [0, 1.0, 0])
        tg = list(tg) + [0] * (3 - len(tg))
        self.tx, self.ty, self.tz = (Prop(tg[i], 0, "target") for i in range(3))
        self.orbit = float(base.get("orbit", 0))
        self.ortho = bool(base.get("ortho", False))
        self.follow = base.get("follow") or None
        self.follow_y = float(base.get("follow_y", 1.0))

    def build(self, lt, d, W, H, actors: dict) -> Camera:
        tx, ty, tz = self.tx(lt, d), self.ty(lt, d), self.tz(lt, d)
        yaw = self.yaw(lt, d) + self.orbit * lt
        fid = self.follow or (next(iter(actors)) if self.preset == "chase" and actors else None)
        a = actors.get(fid) if fid else None
        if a is not None:
            pos = a.world_pos(lt, d)
            tx, tz = pos[0], pos[2]
            ty = pos[1] + self.follow_y * a.pscale(lt, d)
            if self.preset == "chase":
                yaw += a.yaw_at(lt, d) + 180.0
        return Camera(W, H, yaw, self.pitch(lt, d), self.dist(lt, d), self.fov(lt, d), (tx, ty, tz), self.ortho, self.roll(lt, d))


# ======================================================================= actor
class Actor:
    def __init__(self, spec: dict, ctx, index: int):
        th = ctx.theme
        self.ctx = ctx
        self.kind = spec.get("kind", "stickman")
        self.id = str(spec.get("id") or f"actor{index + 1}")
        self.px, self.py, self.pz = _p(spec, "x", 0), _p(spec, "y", 0), _p(spec, "z", 0)
        self.pyaw, self.pscale = _p(spec, "yaw", 0), _p(spec, "scale", 1)
        self.start = float(spec.get("start", 0))
        self.speed = float(spec.get("speed", 1))
        self.face_auto = spec.get("face") == "auto"
        self.color = parse_color(spec.get("color"), th, tuple(th["accent"]))
        self.cols = dict(skin=(255, 208, 170), hair=(35, 22, 60), shirt=self.color, pants=(44, 38, 104), shoes=tuple(th["muted"]))
        if isinstance(spec.get("colors"), dict):
            for k, v in spec["colors"].items():
                if k in self.cols:
                    self.cols[k] = parse_color(v, th, self.cols[k])
        self.lw = float(spec.get("line_width", 0.05))
        self.ring = spec.get("head", "ring") != "solid"
        self.outline = bool(spec.get("outline", self.kind not in ("voxels", "voxel_text")))
        self.enter = spec.get("enter") if spec.get("enter") in ENTERS else None
        self.enter_dur = float(spec.get("enter_dur", 0.7))
        self.spin = float(spec.get("spin", 0))
        self.bob = float(spec.get("bob", 0))
        self.dims = R.STICK_DIMS if self.kind == "stickman" else R.BLOCK_DIMS
        # route
        self.path = [(float(p[0]), float(p[1])) for p in (spec.get("path") or [])]
        self.walk_speed = float(spec.get("walk_speed", 1.4))
        self.path_start = float(spec.get("path_start", self.start))
        self.cum = [0.0]
        for a, b in zip(self.path, self.path[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        # actions
        acts = spec.get("actions")
        if not acts and spec.get("motion"):
            acts = [{"motion": spec["motion"]}]
        self.actions = None
        if acts:
            self.actions, t0 = [], 0.0
            for i, a in enumerate(acts):
                a = a if isinstance(a, dict) else {"motion": a}
                name = R.motion_name(a.get("motion")) or "idle"
                dur = float(a.get("dur", 2.0)) if i < len(acts) - 1 else float(a.get("dur", 1e9))
                self.actions.append(dict(name=name, t0=t0, dur=dur, speed=float(a.get("speed", 1.0))))
                t0 += dur
        # speech bubbles
        say = spec.get("say")
        if isinstance(say, str):
            say = [{"text": say, "start": self.start + 0.3, "dur": 2.2}]
        self.say = [dict(text=str(s.get("text", "")), start=float(s.get("start", 0)), dur=float(s.get("dur", 2.0)))
                    for s in (say or []) if isinstance(s, dict) and s.get("text")]
        # static meshes for props
        self.mesh = None
        self.sphere_r = 0.0
        self.height = 1.9
        self._build_prop(spec, ctx)

    # ---- props
    def _build_prop(self, sp, ctx):
        k, size = self.kind, float(sp.get("size", 1.0))
        c = self.color
        if k == "cube":
            self.mesh, self.height = R.cube_mesh(size, c), size
        elif k == "platform":
            self.mesh, self.height = R.platform_mesh(float(sp.get("w", 3)), float(sp.get("d", 3)), float(sp.get("h", 0.3)), c), float(sp.get("h", 0.3))
        elif k == "pyramid":
            self.mesh, self.height = R.pyramid_mesh(size, c), size
        elif k == "tree":
            self.mesh, self.height = R.tree_mesh(size, c if sp.get("color") else (48, 170, 92)), size * 1.6
        elif k == "sphere":
            self.sphere_r, self.height = size / 2, size
        elif k == "voxels":
            pal = {ch: parse_color(v, ctx.theme, c) for ch, v in (sp.get("palette") or {}).items()}
            cells = R.art_cells([str(r) for r in sp.get("art", [])], pal, int(sp.get("depth", 2)))
            vs = float(sp.get("voxel", 0.12))
            self.mesh = R.voxel_mesh(cells, vs)
            self.height = (max((cc[1] for cc in cells), default=0) + 1) * vs
        elif k == "voxel_text":
            lst = sp.get("colors") if isinstance(sp.get("colors"), list) and sp["colors"] else [sp.get("color")]
            cols = [parse_color(x, ctx.theme, c) if x is not None else c for x in lst]
            cells = R.text_cells(ctx.fk, str(sp.get("text", "TEXT")), int(sp.get("px", 20)), cols, int(sp.get("depth", 3)))
            rows = (max((cc[1] for cc in cells), default=0) + 1)
            vs = float(sp.get("size", 1.0)) / max(1, rows)
            self.mesh = R.voxel_mesh(cells, vs)
            self.height = float(sp.get("size", 1.0))

    # ---- time -> position / facing
    def _path_state(self, lt):
        dist = max(0.0, lt - self.path_start) * self.walk_speed
        total = self.cum[-1]
        if len(self.path) < 2:
            return (self.path[0] if self.path else (0.0, 0.0)), 0.0, False, 0.0
        dd = min(dist, total)
        i = max(0, min(len(self.path) - 2, int(np.searchsorted(self.cum, dd, side="right")) - 1))
        a, b = self.path[i], self.path[i + 1]
        ln = max(1e-9, self.cum[i + 1] - self.cum[i])
        k = (dd - self.cum[i]) / ln
        x, z = a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        yaw = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))
        return (x, z), dd, (0 < dist < total), yaw

    def world_pos(self, lt, d):
        if self.path:
            (x, z), _, _, _ = self._path_state(lt)
            return np.array([x, self.py(lt, d), z])
        return np.array([self.px(lt, d), self.py(lt, d), self.pz(lt, d)])

    def yaw_at(self, lt, d):
        base = self.pyaw(lt, d)
        if self.path and len(self.path) > 1:
            return self._path_state(lt)[3] + base
        if self.face_auto:
            p1 = self.world_pos(lt, d)
            p0 = self.world_pos(lt - 0.05, d) if lt > 0.05 else p1
            if lt <= 0.05:
                p1 = self.world_pos(lt + 0.05, d); p0 = self.world_pos(lt, d)  # noqa: E702
            v = p1 - p0
            if math.hypot(v[0], v[2]) > 1e-4:
                return math.degrees(math.atan2(v[0], v[2])) + base
        return base

    def _speed_xz(self, lt, d):
        p1, p0 = self.world_pos(lt + 0.04, d), self.world_pos(lt - 0.04, d)
        return math.hypot(p1[0] - p0[0], p1[2] - p0[2]) / 0.08

    # ---- pose
    def pose_at(self, lt, d) -> dict:
        t = lt - self.start
        dm = self.dims
        if self.actions:
            i = 0
            for j, a in enumerate(self.actions):
                if t >= a["t0"]:
                    i = j
            a = self.actions[i]
            local = max(0.0, t - a["t0"])
            pose = R.MOTIONS[a["name"]](local * a["speed"] * self.speed, dm)
            if i > 0 and local < 0.25:
                pv = self.actions[i - 1]
                prev = R.MOTIONS[pv["name"]]((local + (a["t0"] - pv["t0"])) * pv["speed"] * self.speed, dm)
                pose = R.blend_pose(prev, pose, R._smooth(0, 0.25, local))
            return pose
        if self.path and len(self.path) > 1:
            _, dist, moving, _ = self._path_state(lt)
            if moving:
                name = "run" if self.walk_speed > 2.6 else "walk"
                return R.MOTIONS[name](dist / R.STRIDE[name] * self.speed, dm)
            return R.MOTIONS["idle"](max(0.0, t), dm)
        if self.face_auto:
            v = self._speed_xz(lt, d)
            if v > 0.08:
                name = "run" if v > 2.6 else "walk"
                return R.MOTIONS[name](lt * v / R.STRIDE[name] * self.speed, dm)
        return R.MOTIONS["idle"](max(0.0, t), dm)

    def visible(self, lt):
        return lt >= self.start

    def _enter(self, lt):
        sc, dy, yaw = 1.0, 0.0, 0.0
        t = lt - self.start
        if self.enter and t < self.enter_dur:
            k = max(0.0, t / self.enter_dur)
            if self.enter == "pop":
                sc = max(0.001, ease("out_back", k))
            elif self.enter == "drop":
                dy = 4.0 * (1 - ease("out_bounce", k))
            elif self.enter == "spin_in":
                e = ease("out_cubic", k); sc = max(0.001, e); yaw = 360 * (1 - e)  # noqa: E702
            elif self.enter == "rise":
                dy = -1.6 * (1 - ease("out_cubic", k)); sc = max(0.001, k)  # noqa: E702
        return sc, dy, yaw

    # ---- draw
    def emit(self, P: Painter, lt, d):
        """Adds geometry to the painter. -> dict(shadow=(x,z,r), anchor=world point above the head)"""
        if not self.visible(lt):
            return None
        esc, edy, eyaw = self._enter(lt)
        pos = self.world_pos(lt, d) + np.array([0, edy, 0])
        yaw = self.yaw_at(lt, d) + eyaw
        sc = self.pscale(lt, d) * esc
        if self.kind in ("stickman", "pixelman"):
            pose = self.pose_at(lt, d)
            yaw += pose.get("yaw", 0.0)
            J, off = R.solve(pose, self.dims)
            xf = R.Xf(pos, yaw, sc, off)
            if self.kind == "stickman":
                R.emit_stickman(P, J, xf, self.color, self.lw, self.ring)
            else:
                R.emit_pixelman(P, J, xf, self.cols, self.outline)
            lift = max(0.0, pose.get("lift", 0.0)) * sc
            anchor = xf.pt(J["head"] + np.array([0, R.HEADR + 0.16, 0]))
            return dict(shadow=(pos[0], pos[2], 0.42 * sc * (1 - min(0.5, lift * 0.25)), 0.42 * (1 - min(0.6, lift * 0.35))), anchor=anchor)
        # props
        yaw += self.spin * (lt - self.start)
        pos = pos + np.array([0, self.bob * math.sin((lt - self.start) * math.tau * 0.8), 0])
        if self.kind == "sphere":
            r = self.sphere_r * sc
            P.add_circle(pos + np.array([0, r, 0]), r, self.color, shaded=True)
            return dict(shadow=(pos[0], pos[2], r * 0.9, 0.38), anchor=pos + np.array([0, 2 * r + 0.15, 0]))
        if self.mesh is None:
            return None
        V, N, C = self.mesh
        if len(V) == 0:
            return None
        Ry = rot_y(yaw)
        Vw = pos + (V.reshape(-1, 3) * sc) @ Ry.T
        P.add_faces(Vw.reshape(-1, 4, 3), N @ Ry.T, C, outline=DARK if self.outline else None)
        ext = float(np.abs(V[..., [0, 2]]).max()) * sc if len(V) else 0.5
        sa = 0.2 if self.kind in ("voxels", "voxel_text") else 0.36
        return dict(shadow=(pos[0], pos[2], min(ext, 2.5) * 0.9, sa), anchor=pos + np.array([0, self.height * sc + 0.15, 0]))


# ======================================================================= stage layer
class Stage3DLayer(Layer):
    kind = "stage3d"

    def __init__(self, spec, ctx, index):
        super().__init__(spec, ctx, index)
        th = ctx.theme
        self.W, self.H = ctx.W, ctx.H
        self.camrig = CameraRig(spec.get("view"))
        self.actors = [Actor(a, ctx, i) for i, a in enumerate(spec.get("actors") or []) if isinstance(a, dict)]
        self.by_id = {a.id: a for a in self.actors}
        self.floor = spec.get("floor", "grid") if spec.get("floor", "grid") in FLOORS else "grid"
        self.floor_color = parse_color(spec.get("floor_color"), th, tuple(th["accent"]))
        self.floor_ext = float(spec.get("floor_ext", 14.0))
        self.floor_step = float(spec.get("floor_step", 1.0))
        self.pixel = max(0, int(spec.get("pixel", 0)))
        self.bloom = max(0.0, min(1.5, float(spec.get("bloom", 0.0))))
        self.ss = float(spec.get("ss") or min(2.0, max(1.0, 3840.0 / max(1, ctx.W))))
        self.th = th

    def is_static(self):
        return False

    def stable(self, base):
        return False

    # ---- speech bubble
    def _bubble(self, canvas, text, ax, ay, k):
        ctx, S = self.ctx, self.ctx.S
        font = ctx.fk.get(text, max(10, int(30 * S)))
        maxw = ctx.W * 0.30
        lines, cur = [], ""
        for w in str(text).split(" "):
            t = (cur + " " + w).strip()
            if font.getlength(t) <= maxw or not cur:
                cur = t
            else:
                lines.append(cur); cur = w  # noqa: E702
        lines.append(cur)
        asc, desc = font.getmetrics()
        lh = asc + desc
        pad = 16 * S
        w = max(font.getlength(s) for s in lines) + 2 * pad
        h = lh * len(lines) + 2 * pad
        tail = 22 * S
        spr = Image.new("RGBA", (int(w + 10 * S), int(h + tail + 8 * S)), (0, 0, 0, 0))
        d = ImageDraw.Draw(spr)
        x0, y0 = 5 * S, 4 * S
        acc = tuple(int(c) for c in self.th["accent"])
        d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=14 * S, fill=(255, 255, 255, 245), outline=acc, width=max(2, int(3 * S)))
        cx = x0 + w / 2
        d.polygon([(cx - 9 * S, y0 + h - 2), (cx + 9 * S, y0 + h - 2), (cx, y0 + h + tail)], fill=(255, 255, 255, 245), outline=acc)
        d.polygon([(cx - 7 * S, y0 + h - 4), (cx + 7 * S, y0 + h - 4), (cx, y0 + h + tail - 4)], fill=(255, 255, 255, 255))
        for i, s in enumerate(lines):
            d.text((x0 + w / 2, y0 + pad + i * lh), s, font=font, fill=(24, 12, 48, 255), anchor="ma")
        place(canvas, spr, ax, ay, k, k, 0.0, 1.0, anchor=(spr.width / 2, spr.height - 2 * S))

    # ---- content
    def sprite(self, lt, d):
        W, H, S = self.W, self.H, self.ctx.S
        px = self.pixel
        if px:
            rw, rh, k = max(8, W // px), max(8, H // px), 1.0
        else:
            rw, rh, k = W, H, self.ss
        cam = self.camrig.build(lt, d, rw, rh, self.by_id)
        img = Image.new("RGBA", (int(rw * k), int(rh * k)), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        P = Painter(cam)
        infos = [(a, a.emit(P, lt, d)) for a in self.actors]
        ext, step = self.floor_ext, self.floor_step
        cx, cz = float(cam.target[0]), float(cam.target[2])
        fc = self.floor_color
        wpx = 1.0 if px else max(1.0, 1.5 * S * k)
        if self.floor == "checker":
            P.floor_checker(dr, k, ext, step, tuple(int(c * 0.30) for c in fc), tuple(int(c * 0.13) + 14 for c in fc), 0.78, cx, cz)
        elif self.floor == "solid":
            P.floor_solid(dr, k, ext, tuple(int(c * 0.22) for c in fc), 0.85)
        if self.floor != "none":
            for a, inf in infos:
                if inf and inf.get("shadow"):
                    sx, sz, sr, sa = inf["shadow"]
                    P.shadow(dr, k, sx, sz, sr, sa)
        if self.floor == "grid":
            P.floor_grid(dr, k, ext, step, fc, 0.62, wpx / k if not px else 1.0, cx, cz)
        P.draw(dr, k, outline_px=1.0 if px else 1.3 * S)
        if px:
            out = img.resize((W, H), Image.NEAREST)
        elif k != 1.0:
            out = img.convert("RGBa").resize((W, H), Image.BOX).convert("RGBA")
        else:
            out = img
        if self.bloom > 0:
            sm = out.resize((max(8, W // 4), max(8, H // 4)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(max(1.5, 2.4 * S)))
            glow = sm.resize((W, H), Image.BILINEAR)
            glow.putalpha(glow.getchannel("A").point(lambda v, g=1 + 2.2 * self.bloom: min(255, int(v * g))))
            res = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            res.alpha_composite(glow)
            res.alpha_composite(out)
            out = res
        # bubbles on top (screen space)
        for a, inf in infos:
            if not inf or not a.say:
                continue
            for s in a.say:
                tt = lt - s["start"]
                if 0 <= tt <= s["dur"]:
                    kk = ease("out_back", min(1.0, tt / 0.25)) * (1.0 if s["dur"] - tt > 0.15 else max(0.0, (s["dur"] - tt) / 0.15))
                    sp, _ = cam.project(np.asarray(inf["anchor"], float))
                    if kk > 0.02:
                        self._bubble(out, s["text"], sp[0][0] * W / rw, sp[0][1] * H / rh, kk)
        return out


# ======================================================================= validation
def validate_stage(sp: dict, tag: str, errs: list, warns: list) -> None:
    view = sp.get("view")
    pre = view if isinstance(view, str) else (view or {}).get("preset") if isinstance(view, dict) else None
    if pre and pre not in VIEW_PRESETS:
        m = difflib.get_close_matches(str(pre), VIEWS, 1)
        errs.append(f"{tag}: unknown view '{pre}'" + (f" - did you mean '{m[0]}'?" if m else f" (valid: {', '.join(VIEWS)})"))
    if isinstance(view, dict):
        for key in ("yaw", "pitch", "dist", "fov", "roll"):
            if key in view and (m := check_prop(view[key], f"{tag}.view.{key}")):
                errs.append(m)
    if sp.get("floor") is not None and sp["floor"] not in FLOORS:
        errs.append(f"{tag}: unknown floor '{sp['floor']}' (valid: {', '.join(FLOORS)})")
    if sp.get("pixel") is not None and not (isinstance(sp["pixel"], (int, float)) and 0 <= sp["pixel"] <= 16):
        errs.append(f"{tag}: 'pixel' must be a number from 0 (smooth) to 16 (very chunky)")
    acts = sp.get("actors")
    if not isinstance(acts, list) or not acts:
        errs.append(f"{tag}: 'actors' must be a non-empty list (e.g. [{{\"kind\": \"stickman\", \"motion\": \"walk\"}}])"); return
    ids = set()
    for i, a in enumerate(acts):
        at = f"{tag} actor {i + 1}"
        if not isinstance(a, dict):
            errs.append(f"{at}: must be an object"); continue  # noqa: E702
        kd = a.get("kind", "stickman")
        if kd not in KINDS:
            m = difflib.get_close_matches(str(kd), KINDS, 1)
            errs.append(f"{at}: unknown kind '{kd}'" + (f" - did you mean '{m[0]}'?" if m else f" (valid: {', '.join(KINDS)})")); continue  # noqa: E702
        if a.get("id"):
            if a["id"] in ids:
                warns.append(f"{at}: duplicate id '{a['id']}'")
            ids.add(a["id"])
        for key in ("x", "y", "z", "yaw", "scale"):
            if key in a and (m := check_prop(a[key], f"{at}.{key}")):
                errs.append(m)
        if a.get("color") is not None and not color_ok(a["color"]):
            warns.append(f"{at}: unreadable color {a['color']!r}")
        names = []
        if a.get("motion"):
            names.append(a["motion"])
        for ac in a.get("actions") or []:
            names.append(ac.get("motion") if isinstance(ac, dict) else ac)
        for nm in names:
            if R.motion_name(nm) is None:
                m = difflib.get_close_matches(str(nm), list(R.MOTIONS) + list(R.ALIASES), 1)
                errs.append(f"{at}: unknown motion '{nm}'" + (f" - did you mean '{m[0]}'?" if m else f" (valid: {', '.join(R.MOTIONS)})"))
        if names and kd not in ("stickman", "pixelman"):
            warns.append(f"{at}: '{kd}' is a prop - motions only apply to stickman / pixelman")
        if kd in ("stickman", "pixelman") and a.get("colors") is not None and not isinstance(a["colors"], dict):
            errs.append(f"{at}: 'colors' must be an object like {{\"skin\": \"#ffd0aa\", \"shirt\": \"pink\"}}")
        if kd == "voxel_text" and a.get("colors") is not None and not isinstance(a["colors"], list):
            errs.append(f"{at}: voxel_text 'colors' must be a list of 1-2 colours (a gradient), e.g. [[255,46,151],[0,240,255]]")
        if a.get("enter") and a["enter"] not in ENTERS:
            errs.append(f"{at}: unknown enter '{a['enter']}' (valid: {', '.join(ENTERS)})")
        pth = a.get("path")
        if pth is not None and not (isinstance(pth, list) and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in pth)):
            errs.append(f"{at}: 'path' must be a list of [x, z] points, e.g. [[-3, 0], [3, 0]]")
        if kd == "voxels":
            art = a.get("art")
            if not (isinstance(art, list) and art and all(isinstance(r, str) for r in art)) or not isinstance(a.get("palette"), dict):
                errs.append(f"{at} (voxels): needs 'art' (list of row strings) and 'palette' ({{\"X\": \"pink\"}})")
            elif sum(len(r) for r in art) > 1600:
                warns.append(f"{at}: very large voxel art - rendering will be slow")
        if kd == "voxel_text":
            if not a.get("text"):
                errs.append(f"{at} (voxel_text): 'text' is required")
            elif len(str(a["text"])) > 24:
                warns.append(f"{at}: long voxel text is slow - keep it under ~24 characters")
        for s in a.get("say") or []:
            if isinstance(s, dict) and not s.get("text"):
                errs.append(f"{at}: every 'say' item needs 'text'")


def catalog() -> dict:
    return dict(kinds=list(KINDS), motions=list(R.MOTIONS), views=list(VIEWS), floors=list(FLOORS), enters=list(ENTERS))
