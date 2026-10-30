#!/usr/bin/env python3
# Copyright (c) 2026 Ahmii Labs
# SPDX-License-Identifier: MIT  (see LICENSE in the project root)
"""Self-check: python tests/smoke_test.py   (no pytest needed; ~1 minute on one core)"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
RENDER = os.path.join(ROOT, "scripts", "render.py")
fails = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))
    if not cond: fails.append(name)


# ---- unit: easing, props, expressions
from mve.easing import EASE_NAMES, ease
check("easing endpoints", all(abs(ease(n, 1) - 1) < 1e-6 and (n == "hold" or abs(ease(n, 0)) < 1e-9) for n in EASE_NAMES))
check("cubic-bezier", abs(ease([0, 0, 1, 1], 0.37) - 0.37) < 0.01)
from mve.props import Prop, check as pcheck
check("keyframes", abs(Prop([[0, 0], [1, 10, "linear"]])(0.5) - 5) < 1e-6 or abs(Prop([[0, 0], [1, 10]])(0.5) - 5) < 0.6)
check("tween", Prop({"from": 0, "to": 4, "start": 1, "dur": 1, "ease": "linear"})(1.5) == 2.0)
check("expression", abs(Prop("2*t+1")(3) - 7) < 1e-9)
check("expression sandbox", all(pcheck(x) for x in ("__import__('os')", "t.real", "[1 for i in t]", "open('f')")))
check("bad keyframe rejected", pcheck([[0, 0], [1, 1, "nope"]]) is not None)
# ---- unit: fonts
from mve.fonts import FontKit
fk = FontKit({})
for sample in ("Motion 123", "چھوٹے قدم، بڑا فرق", "नमस्ते दुनिया"):
    p = fk.path_for(sample); check("font covers " + sample[:6], bool(p) and not fk.missing(p, sample), str(p))
# ---- unit: placement geometry
from PIL import Image
import numpy as np
from mve.compositing import place
cv = Image.new("RGB", (200, 200)); place(cv, Image.new("RGBA", (40, 20), (255, 0, 0, 255)), 100, 100, 1, 1, 90)
ys, xs = np.where(np.asarray(cv)[..., 0] > 128); check("rotate 90 bbox", (xs.min(), xs.max(), ys.min(), ys.max()) == (90, 109, 80, 119))


# ---- resolution parsing + real output sizes
sys.argv = ["x"]; import render as R
check("parse_resolution", [R.parse_resolution(v) for v in ("4k", "1080p", "2k", 720, "3840x2160", "uhd", "abc")] == [2160, 1080, 1440, 720, 2160, 2160, None])
check("aspect_size", R.aspect_size("21:9") == (1680, 720) and R.aspect_size("5:4") == (900, 720) and R.aspect_size("zz") is None)


def still_size(spec, *extra):
    import tempfile as _t; d = _t.mkdtemp(); json.dump(spec, open(os.path.join(d, "s.json"), "w"))
    r = subprocess.run([sys.executable, RENDER, os.path.join(d, "s.json"), "--still", "0.5", "-o", os.path.join(d, "o.png"), *extra], capture_output=True, text=True)
    return Image.open(os.path.join(d, "o.png")).size if os.path.exists(os.path.join(d, "o.png")) else r.stderr[-200:]


base = {"scenes": [{"type": "title", "text": "Hi", "duration": 2}]}
check("default is 1080p landscape", still_size(base) == (1920, 1080), str(still_size(base)))
check("--resolution 4k", still_size(base, "--resolution", "4k") == (3840, 2160))
check("9:16 at 1440", still_size({**base, "aspect": "9:16", "resolution": "2k"}) == (1440, 2560))
check("4:5 at 1080", still_size({**base, "aspect": "4:5"}) == (1080, 1350))
check("draft stays small", max(still_size({**base, "resolution": "4k"}, "--quality", "draft")) <= 640)

# ---- unit: 3D stage (camera, rig ground lock, templates, validation)
import numpy as np
from mve import rigs as RG
from mve.geo3d import Camera
cam = Camera(1280, 720, yaw=30, pitch=15, dist=6, fov=40, target=(0, 1, 0))
pt, zz = cam.project(np.array([[0.0, 1.0, 0.0]]))
check("3d camera centres its target", abs(pt[0][0] - 640) < 1 and abs(pt[0][1] - 360) < 1 and abs(zz[0] - 6) < 1e-6)
rig_ok, rig_info = True, ""
for dims in (RG.STICK_DIMS, RG.BLOCK_DIMS):
    for name, fn in RG.MOTIONS.items():
        for t in (0.0, 0.13, 0.37, 0.61, 0.9, 1.4):
            pose = fn(t, dims); J, off = RG.solve(pose, dims)
            pts = [J[k] for k in ("anL", "anR", "wrL", "wrR", "head", "knL", "knR")]
            if not np.isfinite(off) or not all(np.isfinite(p).all() for p in pts) or J["anL"][1] - dims["foot"] + off < pose.get("lift", 0) - 1e-6 - 0.2:
                rig_ok, rig_info = False, f"{name} t={t}"
check("every motion solves, stays finite and above the floor", rig_ok, rig_info)
check("motion aliases", RG.motion_name("cheer") == "victory" and RG.motion_name("nope") is None)
bad3d = {"scenes": [{"type": "world3d", "view": "sideways", "actors": [{"kind": "stickmann", "motion": "wlak"}, {"kind": "pixelman", "motion": "wlak"}]}]}
d3 = tempfile.mkdtemp(); json.dump(bad3d, open(os.path.join(d3, "b3.json"), "w"))
r3 = subprocess.run([sys.executable, RENDER, os.path.join(d3, "b3.json"), "--validate"], capture_output=True, text=True)
check("validate catches 3D typos (view / kind / motion)", '"ok": false' in r3.stdout and "stickman" in r3.stdout and "walk" in r3.stdout and "sideways" in r3.stdout, r3.stdout[-300:])
cat = subprocess.run([sys.executable, RENDER, "--catalog"], capture_output=True, text=True).stdout
check("--catalog lists 3D names", "pixelman" in cat and "voxel_text" in cat and "flip" in cat)
for tpl in ("cyber_intro", "stickman_story", "pixel_party", "voxel_logo"):
    out = os.path.join(d3, tpl + ".json")
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "new_video.py"), tpl, "-o", out], capture_output=True, text=True)
    rr = subprocess.run([sys.executable, RENDER, out, "--validate"], capture_output=True, text=True)
    check("template " + tpl + " validates", os.path.exists(out) and '"ok": true' in rr.stdout, rr.stdout[-200:])

# ---- integration: validate + still for every example, then one tiny silent render
def run(*a):
    return subprocess.run([sys.executable, RENDER, *a], capture_output=True, text=True)


tmp = tempfile.mkdtemp()
for f in sorted(os.listdir(os.path.join(ROOT, "assets"))):
    if not f.startswith("example_") or not f.endswith(".json"): continue
    path = os.path.join(ROOT, "assets", f); spec = json.load(open(path, encoding="utf-8"))
    if spec.get("input_video"): continue                       # needs a real video
    r = run(path, "--validate"); rep = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"ok": False}
    check("validate " + f, rep.get("ok"), r.stderr[-200:] or r.stdout[-200:])
    r = run(path, "--still", "1.2", "--quality", "draft", "-o", os.path.join(tmp, f + ".png"))
    check("still " + f, os.path.exists(os.path.join(tmp, f + ".png")), r.stderr[-300:])
spec = {"output": os.path.join(tmp, "t.mp4"), "aspect": "9:16", "fps": 15, "sfx": True, "scenes": [
    {"type": "kinetic", "text": "TEST", "duration": 2.2, "fx": [{"type": "glow"}]},
    {"type": "logo_reveal", "text": "BRAND", "duration": 3.4, "transition": "glitch"}]}
json.dump(spec, open(os.path.join(tmp, "t.json"), "w"))
r = run(os.path.join(tmp, "t.json"), "--quality", "draft")
check("render mp4", os.path.exists(spec["output"]) and os.path.getsize(spec["output"]) > 5000, r.stderr[-300:])
spec3 = json.load(open(os.path.join(ROOT, "assets", "example_3d_world.json"))); spec3["output"] = os.path.join(tmp, "t3d.mp4")
json.dump(spec3, open(os.path.join(tmp, "t3d.json"), "w"))
r = run(os.path.join(tmp, "t3d.json"), "--quality", "draft", "--workers", "2")
check("render 3D mp4 (2 workers)", os.path.exists(spec3["output"]) and os.path.getsize(spec3["output"]) > 5000, r.stderr[-300:])
bad = {"scenes": [{"type": "kinetic", "text": "x", "preset": "trackng"}, {"type": "motion", "layers": [{"type": "shape", "shape": "rect", "x": "__import__('os')"}]}]}
json.dump(bad, open(os.path.join(tmp, "b.json"), "w")); r = run(os.path.join(tmp, "b.json"), "--validate")
check("validate catches typos/unsafe expr", '"ok": false' in r.stdout and "trackng" in r.stdout, r.stdout[-300:])
print("\n%s" % ("ALL OK" if not fails else "FAILED: " + ", ".join(fails))); sys.exit(1 if fails else 0)
