#!/usr/bin/env python3
"""Render the AI Verse faceless video from narration audio + B-roll stills.

Pipeline per scene:
  still image -> Ken Burns zoompan -> stat-card overlay (PIL/alpha) -> burned captions
  then all scenes concatenated with the narration bed.

Usage: python3 aiverse/video/build_video.py
"""
import json
import os
import re
import subprocess
import textwrap

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

FF = imageio_ffmpeg.get_ffmpeg_exe()
HERE = os.path.dirname(os.path.abspath(__file__))
AUD = os.path.join(HERE, "audio")
BROLL = os.path.join(HERE, "broll")
WORK = os.path.join(HERE, "work")
OUT = os.path.join(os.path.dirname(HERE), "downloads")

W, H = 1920, 1080
FPS = 30
CYAN = (0, 210, 255)
RED = (230, 60, 60)
WHITE = (255, 255, 255)


def font(size, bold=True):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def dur(path):
    out = subprocess.run(
        [FF, "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


# ---------------------------------------------------------------- overlays
def fit_font(d, text, max_w, start, floor=40):
    """Shrink the font until the string fits inside max_w."""
    size = start
    while size > floor:
        f = font(size)
        b = d.textbbox((0, 0), text, font=f)
        if b[2] - b[0] <= max_w:
            return f
        size -= 4
    return font(floor)


def make_stat_card(text, path, sub=None, kind="stat"):
    """Transparent PNG overlay with the big on-screen number/text."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "stat":
        f = fit_font(d, text, W - 340, 150)
        bbox = d.textbbox((0, 0), text, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x, y = (W - tw) / 2, (H - th) / 2 - 60
        # dark plate behind for legibility
        d.rounded_rectangle([x - 60, y - 55, x + tw + 60, y + th + 60],
                            radius=28, fill=(0, 0, 0, 190))
        d.text((x + 5, y + 5), text, font=f, fill=(0, 0, 0, 200))
        d.text((x, y), text, font=f, fill=CYAN + (255,))
        if sub:
            fs = fit_font(d, sub, W - 400, 52, floor=28)
            b2 = d.textbbox((0, 0), sub, font=fs)
            d.text(((W - (b2[2] - b2[0])) / 2, y + th + 95), sub,
                   font=fs, fill=WHITE + (230,))
    elif kind == "title":
        num, label = text.split("|")
        f = fit_font(d, label, W - 400, 110, floor=44)
        fnum = font(190)
        bn = d.textbbox((0, 0), num, font=fnum)
        bl = d.textbbox((0, 0), label, font=f)
        total_w = max(bn[2] - bn[0], bl[2] - bl[0])
        x = (W - total_w) / 2
        d.rectangle([x - 80, H / 2 - 230, x + total_w + 80, H / 2 + 230],
                    fill=(0, 0, 0, 205))
        d.text(((W - (bn[2] - bn[0])) / 2, H / 2 - 200), num,
               font=fnum, fill=CYAN + (255,))
        d.text(((W - (bl[2] - bl[0])) / 2, H / 2 + 40), label,
               font=f, fill=WHITE + (255,))
        d.rectangle([x - 80, H / 2 + 200, x + total_w + 80, H / 2 + 214],
                    fill=CYAN + (255,))
    elif kind == "lower":
        f = fit_font(d, text, W - 300, 58, floor=30)
        bbox = d.textbbox((0, 0), text, font=f)
        tw = bbox[2] - bbox[0]
        d.rectangle([80, H - 300, 80 + tw + 70, H - 200], fill=RED + (225,))
        d.text((115, H - 285), text, font=f, fill=WHITE + (255,))
    img.save(path)
    return path


# ---------------------------------------------------------------- captions
def srt_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60)
    s = t % 60
    return "%02d:%02d:%06.3f" % (h, m, s) if False else \
        "%02d:%02d:%02d,%03d" % (h, m, int(s), round((s - int(s)) * 1000))


def build_srt(text, duration, path):
    """Distribute caption chunks evenly across the segment duration."""
    words = text.split()
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) > 42 or w.endswith((".", "?", "!", "—")):
            chunks.append(" ".join(cur)); cur = []
    if cur:
        chunks.append(" ".join(cur))
    total_chars = sum(len(c) for c in chunks) or 1
    lines, t = [], 0.0
    for i, c in enumerate(chunks, 1):
        seg = duration * (len(c) / total_chars)
        lines.append("%d\n%s --> %s\n%s\n" %
                     (i, srt_time(t), srt_time(min(t + seg, duration)),
                      "\n".join(textwrap.wrap(c, 38))))
        t += seg
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------- scene render
def render_scene(idx, image, audio, text, overlays, out_path):
    d = dur(audio)
    frames = int(d * FPS) + 1
    srt = build_srt(text, d, os.path.join(WORK, "s%02d.srt" % idx))

    # Ken Burns: slow push in
    vf = [
        "scale=%d:%d:force_original_aspect_ratio=increase" % (W * 2, H * 2),
        "crop=%d:%d" % (W * 2, H * 2),
        ("zoompan=z='min(zoom+0.0007,1.18)':d=%d:x='iw/2-(iw/zoom/2)'"
         ":y='ih/2-(ih/zoom/2)':s=%dx%d:fps=%d" % (frames, W, H, FPS)),
        # subtle cinematic grade
        "eq=contrast=1.08:saturation=1.15:brightness=-0.02",
        "vignette=PI/5",
    ]
    inputs = ["-loop", "1", "-t", "%.3f" % d, "-i", image]
    filter_chain = "[0:v]" + ",".join(vf) + "[bg]"
    last = "bg"
    for n, (ov_png, start, length) in enumerate(overlays):
        inputs += ["-loop", "1", "-t", "%.3f" % d, "-i", ov_png]
        nxt = "v%d" % n
        filter_chain += (";[%s][%d:v]overlay=0:0:enable='between(t,%.2f,%.2f)'"
                         "[%s]" % (last, n + 1, start, start + length, nxt))
        last = nxt
    sub_style = ("FontName=DejaVu Sans,FontSize=17,Bold=1,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H00000000,BackColour=&HA0000000,BorderStyle=3,"
                 "Outline=2,Shadow=0,MarginV=60,Alignment=2")
    filter_chain += ";[%s]subtitles=%s:force_style='%s'[vout]" % (
        last, srt.replace(":", r"\:"), sub_style)

    cmd = [FF, "-y"] + inputs + ["-i", audio,
           "-filter_complex", filter_chain,
           "-map", "[vout]", "-map", "%d:a" % (len(overlays) + 1),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-shortest", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path, d


def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(HERE, "scenes.json"), encoding="utf-8") as f:
        scenes = json.load(f)

    parts, total = [], 0.0
    for i, sc in enumerate(scenes):
        audio = os.path.join(AUD, sc["audio"])
        if not os.path.exists(audio):
            print("SKIP (missing audio):", sc["audio"])
            continue
        ovs = []
        for j, ov in enumerate(sc.get("overlays", [])):
            p = os.path.join(WORK, "ov_%02d_%d.png" % (i, j))
            make_stat_card(ov["text"], p, ov.get("sub"), ov.get("kind", "stat"))
            ovs.append((p, ov.get("at", 1.0), ov.get("len", 3.0)))
        out = os.path.join(WORK, "scene_%02d.mp4" % i)
        _, d = render_scene(i, os.path.join(BROLL, sc["image"]), audio,
                            sc["text"], ovs, out)
        total += d
        parts.append(out)
        print("scene %02d  %5.1fs  %s" % (i, d, sc["audio"]))

    lst = os.path.join(WORK, "concat.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write("file '%s'\n" % p)
    final = os.path.join(OUT, "AI-Verse-5-Secret-AI-Tools-2026.mp4")
    subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", final], check=True, capture_output=True)
    print("\nFINAL: %s  (%d:%02d)" % (final, int(total // 60), int(total % 60)))


if __name__ == "__main__":
    main()
