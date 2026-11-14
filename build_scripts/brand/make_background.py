"""Render the default MaoKaraoke background loop: pixel-art neon rooftops.

Run from the repository root (needs ffmpeg on PATH):
    python build_scripts/brand/make_background.py

Every animated element repeats a whole number of times per loop, so the last
frame flows straight into the first and the video loops without a seam.
"""

import math
import random
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "pikaraoke" / "static" / "video" / "neon_rooftops.mp4"

W, H = 320, 180
SCALE = 4
FPS = 24
LOOP = 24  # seconds
FRAMES = FPS * LOOP

SKY = [
    (14, 10, 38),
    (20, 13, 50),
    (28, 16, 62),
    (38, 19, 74),
    (52, 22, 86),
    (70, 26, 96),
    (92, 32, 104),
    (118, 40, 110),
    (146, 52, 112),
]
FAR = (44, 24, 72)
FAR_WIN = (96, 60, 120)
NEAR = (22, 13, 40)
ROOF = (12, 8, 24)
WINDOW_COLORS = [(255, 205, 120), (255, 150, 190), (130, 230, 215), (255, 230, 170)]
PINK = (255, 92, 170)
CYAN = (90, 240, 230)
CAT = (8, 5, 16)

rng = random.Random(7)


def wave(t, cycles, phase=0.0):
    """0..1 oscillation that completes `cycles` whole cycles per loop."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * (cycles * t / LOOP + phase))


def mix(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


# ---- static layout -------------------------------------------------------
stars = [
    (rng.randrange(W), rng.randrange(0, 95), rng.choice([1, 2, 3]), rng.random(), rng.random() < 0.15)
    for _ in range(90)
]


def make_buildings(y_base, min_h, max_h, min_w, max_w, seed):
    r = random.Random(seed)
    x, out = -4, []
    while x < W + 4:
        w = r.randrange(min_w, max_w)
        h = r.randrange(min_h, max_h)
        out.append((x, y_base - h, w, h, r.random()))
        x += w + r.choice([0, 0, 1, 2])
    return out


far = make_buildings(150, 30, 75, 14, 30, 11)
near = make_buildings(170, 25, 60, 20, 42, 23)

near_windows = []
for bx, by, bw, bh, _ in near:
    for wy in range(by + 5, by + bh - 4, 6):
        for wx in range(bx + 3, bx + bw - 3, 5):
            if rng.random() < 0.55:
                near_windows.append(
                    (wx, wy, rng.choice(WINDOW_COLORS), rng.choice([0, 1, 2, 3]), rng.random())
                )

far_windows = [
    (bx + 2 + 3 * i, by + 4 + 4 * j)
    for bx, by, bw, bh, _ in far
    for i in range(max(0, (bw - 3) // 3))
    for j in range(max(0, (bh - 6) // 4))
    if rng.random() < 0.18
]

clouds = [(rng.randrange(160), rng.randrange(25, 70), rng.randrange(18, 34)) for _ in range(3)]

notes = [(rng.randrange(20, W - 20), rng.random(), rng.choice([CYAN, PINK, (255, 205, 120)])) for _ in range(7)]

NOTE = ["..XX", "..XX", "..X.", "..X.", "XXX.", "XXX."]
NEON_CAT = [
    "X.......X",
    "XX.....XX",
    "X.XXXXX.X",
    "X.......X",
    "X.X...X.X",
    "X.......X",
    "X...X...X",
    ".X.....X.",
    "..XXXXX..",
]
ROOF_CAT = [
    ".X...X.....",
    ".XX.XX.....",
    ".XXXXX.....",
    ".XXXXX.....",
    "..XXX......",
    ".XXXXX.....",
    "XXXXXXX....",
    "XXXXXXX....",
    "XXXXXXXX...",
]
TAILS = [
    ["........XX", ".......X..", ".......X..", "......X...", "....XX...."],
    [".........X", "........X.", ".......X..", "......X...", "....XX...."],
    ["..........", "........XX", ".......X..", "......X...", "....XX...."],
]


def blit(draw, pattern, x0, y0, color, scale=1):
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch == "X":
                px, py = x0 + x * scale, y0 + y * scale
                draw.rectangle((px, py, px + scale - 1, py + scale - 1), fill=color)


def make_sky():
    """Banded sky with a dithered edge between bands."""
    sky = Image.new("RGB", (W, H))
    band = 150 / len(SKY)
    for y in range(150):
        i = min(len(SKY) - 1, int(y / band))
        frac = y / band - i
        for x in range(W):
            use_next = i + 1 < len(SKY) and frac > 0.75 and (x + y) % 2 == 0
            sky.putpixel((x, y), SKY[i + 1] if use_next else SKY[i])
    return sky


SKY_IMAGE = make_sky()


def render(frame):
    t = frame / FPS
    img = SKY_IMAGE.copy()
    d = ImageDraw.Draw(img)

    # Stars twinkle at whole-cycle rates.
    for sx, sy, cycles, phase, big in stars:
        k = wave(t, cycles, phase)
        if k < 0.25:
            continue
        c = mix((80, 60, 120), (255, 245, 220), k)
        d.point((sx, sy), fill=c)
        if big and k > 0.7:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                d.point((sx + dx, sy + dy), fill=mix(SKY[0], c, 0.6))

    # Shooting stars at fixed points in the loop.
    for start, x0, y0 in ((4.0, 250, 8), (15.5, 140, 14)):
        p = (t - start) / 0.9
        if 0 <= p <= 1:
            hx, hy = int(x0 - 90 * p), int(y0 + 35 * p)
            for k in range(14):
                c = mix(SKY[1], (255, 250, 230), 1 - k / 14)
                d.point((hx + k * 2, hy - int(k * 0.8)), fill=c)

    # Clouds drift one 160px tile per loop.
    shift = 160 * t / LOOP
    for cx, cy, cw in clouds:
        for rep in (-160, 0, 160, 320):
            x = int(cx + rep - shift)
            col = SKY[3]
            d.rectangle((x, cy, x + cw, cy + 2), fill=col)
            d.rectangle((x + 4, cy - 2, x + cw - 6, cy - 1), fill=col)
            d.rectangle((x + 9, cy - 4, x + cw - 12, cy - 3), fill=col)

    # Far skyline.
    for bx, by, bw, bh, _ in far:
        d.rectangle((bx, by, bx + bw - 1, 150), fill=FAR)
    for i, (wx, wy) in enumerate(far_windows):
        if wave(t, 1 + i % 3, i * 0.13) > 0.2:
            d.point((wx, wy), fill=FAR_WIN)
    d.rectangle((0, 150, W, H), fill=FAR)

    # Near skyline with antennas and lit windows.
    for bx, by, bw, bh, r in near:
        d.rectangle((bx, by, bx + bw - 1, H), fill=NEAR)
        if r > 0.6:
            ax = bx + bw // 2
            d.line((ax, by - 7, ax, by - 1), fill=NEAR)
            if wave(t, 12, r) > 0.5:
                d.point((ax, by - 8), fill=(255, 70, 90))
    for wx, wy, color, cycles, phase in near_windows:
        on = cycles == 0 or wave(t, cycles, phase) > 0.35
        d.rectangle((wx, wy, wx + 1, wy + 2), fill=color if on else (40, 26, 60))

    # Neon cat sign on the tallest near building, with an occasional flicker.
    tall = min(near, key=lambda b: b[1])
    sx, sy = tall[0] + tall[2] // 2 - 4, tall[1] + 6
    flicker = not (2.0 < t % 6 < 2.12 or 2.25 < t % 6 < 2.3)
    glow = mix(NEAR, PINK, 0.35)
    for gx, gy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        blit(d, NEON_CAT, sx + gx, sy + gy, glow if flicker else NEAR)
    blit(d, NEON_CAT, sx, sy, PINK if flicker else (90, 40, 70))
    # "KTV"-style bar under the sign, colour-cycling.
    bar = mix(CYAN, PINK, wave(t, 3))
    d.rectangle((sx - 1, sy + 11, sx + 9, sy + 11), fill=bar)

    # Foreground rooftop ledge with a cat watching the city.
    d.rectangle((0, 168, W, H), fill=ROOF)
    d.line((0, 167, W, 167), fill=(40, 24, 60))
    cat_x, cat_y = 36, 168 - 2 * len(ROOF_CAT)
    tail = TAILS[int(abs(math.sin(2 * math.pi * 6 * t / LOOP)) * 2.99)]
    rim = (150, 80, 160)  # moonless city glow on the cat's outline
    for ox, oy in ((-1, 0), (1, 0), (0, -1)):
        blit(d, ROOF_CAT, cat_x + ox, cat_y + oy, rim, 2)
        blit(d, tail, cat_x + 2 + ox, 168 - 2 * len(tail) - 2 + oy, rim, 2)
    blit(d, ROOF_CAT, cat_x, cat_y, CAT, 2)
    blit(d, tail, cat_x + 2, 168 - 2 * len(tail) - 2, CAT, 2)
    if wave(t, 4, 0.1) > 0.04:  # blink
        eye = (255, 205, 120)
        d.rectangle((cat_x + 4, cat_y + 6, cat_x + 5, cat_y + 7), fill=eye)
        d.rectangle((cat_x + 8, cat_y + 6, cat_x + 9, cat_y + 7), fill=eye)

    # Music notes rise from the rooftop and fade into the sky.
    for i, (nx, phase, color) in enumerate(notes):
        p = (t / LOOP * 3 + phase) % 1.0
        y = int(165 - p * 120)
        x = int(nx + 6 * math.sin(2 * math.pi * (p * 2 + i * 0.3)))
        fade = min(1.0, p * 5) * (1 - p)
        bg = img.getpixel((max(0, min(W - 1, x)), max(0, min(H - 1, y))))
        blit(d, NOTE, x, y, mix(bg, color, min(1.0, fade * 1.6)))

    return img


def main():
    ffmpeg = subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-vf", f"scale={W * SCALE}:{H * SCALE}:flags=neighbor",
            "-c:v", "libx264", "-preset", "slow", "-tune", "animation", "-crf", "24",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(OUT),
        ],
        stdin=subprocess.PIPE,
    )
    for frame in range(FRAMES):
        ffmpeg.stdin.write(render(frame).tobytes())
    ffmpeg.stdin.close()
    ffmpeg.wait()
    if ffmpeg.returncode:
        raise SystemExit("ffmpeg failed")
    if len(sys.argv) > 1:  # optional still preview: make_background.py preview.png
        render(int(FPS * 4.3)).resize((W * SCALE, H * SCALE), Image.NEAREST).save(sys.argv[1])


if __name__ == "__main__":
    main()
