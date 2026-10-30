#!/usr/bin/env python3
# Copyright (c) 2026 Ahmii Labs
# SPDX-License-Identifier: MIT  (see LICENSE in the project root)
"""Make a ready-to-render spec.json from a template - no JSON writing needed.

  python scripts/new_video.py --list
  python scripts/new_video.py stickman_story --title "AHMII LABS" --subtitle "Video Editor & Generator Skill" -o my_video.json
  python scripts/render.py my_video.json

Templates: cyber_intro, stickman_story, pixel_party, voxel_logo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


def _base(a, name: str) -> dict:
    return {"output": f"{name}.mp4", "aspect": a.aspect, "resolution": a.resolution, "fps": 30, "auto_duration": False,
            "theme": a.theme, "sfx": True, "transition": "glitch",
            "fx": [{"type": "vignette", "strength": 0.55}, {"type": "grain", "strength": 0.05}]}


def _vox_size(text: str, cap: float = 1.6) -> float:
    return round(min(cap, 6.4 / (0.8 * max(1, len(text)))), 2)


def t_cyber_intro(a) -> dict:
    lines = [x.strip() for x in a.lines.split(",") if x.strip()][:4] or ["Edit.", "Generate.", "Animate."]
    sp = _base(a, "cyber_intro")
    sp.update(bg_style="grid", camera="push_in")
    sp["scenes"] = [
        {"type": "kinetic", "text": a.title, "preset": "tracking", "size": 118, "duration": 3.0, "bg": 0,
         "fx": [{"type": "glow", "amount": 0.9}, {"type": "rgb_split", "start": 0.0, "dur": 0.6, "pulse": True, "distance": 16},
                {"type": "glitch", "start": 0.1, "dur": 0.4}]},
        {"type": "kinetic", "lines": lines, "size": 96, "line_delay": 0.8, "preset": "mask_up", "duration": 0.8 * len(lines) + 1.0,
         "bg": 1, "transition": "whip", "fx": [{"type": "glow", "amount": 0.7}]},
        {"type": "logo_reveal", "text": a.title.title(), "subtext": a.subtitle, "duration": 3.0, "bg": 0, "transition": "flash", "hold_end": True,
         "fx": [{"type": "glow", "amount": 0.8}]},
    ]
    return sp


def t_stickman_story(a) -> dict:
    sp = _base(a, "stickman_story")
    sp.update(bg_style="stars")
    cyan, pink = [0, 240, 255], [255, 46, 151]
    sp["scenes"] = [
        {"type": "world3d", "duration": 4.0, "view": {"preset": "three_quarter", "dist": 5.8, "target": [0, 1, 0]}, "bloom": 0.6, "bg": 0,
         "text": a.title, "size": 56, "text_y": 0.9,
         "actors": [{"kind": "stickman", "color": cyan, "path": [[-4.6, 0], [-1.0, 0]], "walk_speed": 1.8, "speed": 0.92,
                     "actions": [{"motion": "walk", "dur": 2.0}, {"motion": "wave"}],
                     "say": [{"text": a.hello, "start": 2.3, "dur": 1.6}]}]},
        {"type": "world3d", "duration": 4.0, "view": {"preset": "three_quarter", "orbit": 9, "dist": 6.4, "target": [0, 1, 0]}, "bloom": 0.5, "bg": 1,
         "transition": "whip", "transition_duration": 0.4,
         "actors": [{"kind": "stickman", "x": -1.7, "color": cyan, "actions": [{"motion": "jump", "dur": 2.0}, {"motion": "dance"}]},
                    {"kind": "pixelman", "x": 1.5, "color": pink, "enter": "drop", "actions": [{"motion": "wave", "dur": 2.0}, {"motion": "dance"}],
                     "say": [{"text": a.reply, "start": 1.0, "dur": 2.4}]}]},
        {"type": "logo_reveal", "text": a.title.title(), "subtext": a.subtitle, "duration": 3.0, "bg": 0, "transition": "flash", "hold_end": True,
         "fx": [{"type": "glow", "amount": 0.8}]},
    ]
    return sp


def t_pixel_party(a) -> dict:
    sp = _base(a, "pixel_party")
    sp.update(bg_style="pixel_sky")
    pal = {"X": [255, 46, 151], "o": [255, 200, 220]}
    heart = [".XX..XX.", "XXXXXXXX", "XXXXXXXX", ".XXXXXX.", "..XXXX..", "...XX..."]
    sp["scenes"] = [
        {"type": "world3d", "duration": 4.5, "view": {"preset": "iso", "dist": 9.2, "target": [0, 0.8, 0]}, "pixel": 4, "floor": "checker", "floor_ext": 10, "bg": 0,
         "text": a.title, "size": 54, "text_y": 0.9,
         "actors": [{"kind": "pixelman", "x": -2.0, "z": 0.4, "color": [0, 240, 255], "motion": "dance", "enter": "drop"},
                    {"kind": "pixelman", "x": 0.0, "z": -0.8, "color": [255, 46, 151], "motion": "jump", "start": 0.3, "enter": "drop"},
                    {"kind": "pixelman", "x": 2.0, "z": 0.4, "color": [255, 210, 80], "motion": "victory", "start": 0.6, "enter": "drop"},
                    {"kind": "voxels", "x": 0.0, "z": -3.2, "voxel": 0.2, "depth": 2, "art": heart, "palette": pal, "spin": 45, "bob": 0.25},
                    {"kind": "tree", "x": -3.6, "z": -2.0, "size": 1.3}, {"kind": "tree", "x": 3.6, "z": -2.2, "size": 1.1}]},
        {"type": "logo_reveal", "text": a.title.title(), "subtext": a.subtitle, "duration": 3.0, "bg": 1, "transition": "pixelate", "hold_end": True,
         "fx": [{"type": "glow", "amount": 0.7}]},
    ]
    return sp


def t_voxel_logo(a) -> dict:
    sp = _base(a, "voxel_logo")
    sp.update(bg_style="stars")
    size = _vox_size(a.title)
    sp["scenes"] = [
        {"type": "world3d", "duration": 5.0, "view": {"yaw": "30*sin(t*0.9)", "pitch": 14, "dist": 7.8, "target": [0, size / 2, 0]}, "bloom": 0.55,
         "floor": "grid", "bg": 0, "subtext": a.subtitle,
         "actors": [{"kind": "voxel_text", "text": a.title, "size": size, "depth": 3, "px": 22, "colors": [[255, 46, 151], [0, 240, 255]],
                     "enter": "drop", "y": 0}]},
        {"type": "logo_reveal", "text": a.title.title(), "subtext": a.subtitle, "duration": 3.0, "bg": 1, "transition": "flash", "hold_end": True,
         "fx": [{"type": "glow", "amount": 0.8}]},
    ]
    return sp


TEMPLATES = {
    "cyber_intro": (t_cyber_intro, "2D kinetic title + 3 beat lines + logo reveal (~9 s)"),
    "stickman_story": (t_stickman_story, "a stickman walks in and waves, a pixel-man replies, logo (~11 s)"),
    "pixel_party": (t_pixel_party, "pixel-art isometric dance party with a spinning heart (~7.5 s)"),
    "voxel_logo": (t_voxel_logo, "your title as orbiting 3D voxel blocks, then logo reveal (~8 s)"),
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a spec.json from a template")
    ap.add_argument("template", nargs="?", help="one of: " + ", ".join(TEMPLATES))
    ap.add_argument("--list", action="store_true", help="show the templates")
    ap.add_argument("--title", default="AHMII LABS")
    ap.add_argument("--subtitle", default="Video Editor & Generator Skill")
    ap.add_argument("--hello", default="Hi! I make videos from text.", help="what the stickman says (stickman_story)")
    ap.add_argument("--reply", default="Let's make one!", help="what the pixel-man says (stickman_story)")
    ap.add_argument("--lines", default="Edit.,Generate.,Animate.", help="comma-separated beat words (cyber_intro)")
    ap.add_argument("--theme", default="cyber")
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--resolution", type=int, default=1080, help="short side in pixels: 720, 1080, 1440 (2K), 2160 (4K)")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    if a.list or not a.template:
        print("Templates:")
        for k, (_, d) in TEMPLATES.items():
            print(f"  {k:15s} {d}")
        print("\nExample:\n  python scripts/new_video.py stickman_story --title \"MY BRAND\" -o my_video.json\n  python scripts/render.py my_video.json")
        return
    if a.template not in TEMPLATES:
        sys.exit(f"unknown template '{a.template}'. Choose one of: {', '.join(TEMPLATES)}")
    spec = TEMPLATES[a.template][0](a)
    out = a.out or f"{a.template}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
    print(f"saved {out}\nnext:  python scripts/render.py {out} --validate\n       python scripts/render.py {out} --quality draft\n       python scripts/render.py {out}")


if __name__ == "__main__":
    main()
