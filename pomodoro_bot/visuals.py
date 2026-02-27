"""
Hourglass image generator for Pomodoro bot.
Creates beautiful dark-themed hourglass images with sand animation.
"""

import io
import math
import random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT = 600, 900

# Color palettes per phase
PALETTES = {
    "focus": {
        "bg":       (10, 10, 25),
        "bg2":      (20, 15, 40),
        "sand_hi":  (255, 200, 80),
        "sand_lo":  (220, 120, 30),
        "glass":    (80, 120, 220),
        "glass2":   (40, 70, 160),
        "glow":     (255, 130, 30),
        "text":     (255, 210, 110),
        "sub":      (180, 140, 80),
        "label":    "ФОКУС",
        "emoji":    "🍅",
        "star":     True,
    },
    "short_break": {
        "bg":       (10, 22, 12),
        "bg2":      (15, 35, 18),
        "sand_hi":  (140, 255, 160),
        "sand_lo":  (50, 180, 80),
        "glass":    (50, 180, 70),
        "glass2":   (30, 110, 50),
        "glow":     (60, 220, 90),
        "text":     (150, 255, 170),
        "sub":      (80, 180, 100),
        "label":    "ПЕРЕРЫВ",
        "emoji":    "☕",
        "star":     False,
    },
    "long_break": {
        "bg":       (12, 8, 30),
        "bg2":      (20, 12, 50),
        "sand_hi":  (200, 150, 255),
        "sand_lo":  (120, 60, 220),
        "glass":    (110, 70, 210),
        "glass2":   (70, 40, 150),
        "glow":     (160, 90, 255),
        "text":     (210, 170, 255),
        "sub":      (140, 100, 200),
        "label":    "ДЛИННЫЙ ПЕРЕРЫВ",
        "emoji":    "🌙",
        "star":     True,
    },
}


def lerp_color(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _draw_gradient_bg(img: Image.Image, pal: dict):
    """Draw vertical gradient background."""
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        color = lerp_color(pal["bg2"], pal["bg"], t)
        draw.line([(0, y), (WIDTH, y)], fill=color)


def _draw_stars(img: Image.Image, pal: dict, seed: int = 42):
    """Sprinkle random small stars."""
    draw = ImageDraw.Draw(img)
    rng = random.Random(seed)
    for _ in range(120):
        x = rng.randint(0, WIDTH - 1)
        y = rng.randint(0, HEIGHT - 1)
        brightness = rng.randint(80, 220)
        size = rng.choice([1, 1, 1, 2])
        color = (brightness, brightness, int(brightness * 0.85))
        if size == 1:
            draw.point((x, y), fill=color)
        else:
            draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=color)


def _hourglass_x_bounds(y: int, cx: int, top_y: int, mid_y: int, bot_y: int,
                         wide: int, neck: int) -> tuple[int, int]:
    """Return (x_left, x_right) of the hourglass inner edge at row y."""
    if y <= mid_y:
        t = (y - top_y) / max(mid_y - top_y, 1)
        half = wide - (wide - neck) * t
    else:
        t = (y - mid_y) / max(bot_y - mid_y, 1)
        half = neck + (wide - neck) * t
    return int(cx - half), int(cx + half)


def _draw_hourglass_body(draw: ImageDraw.Draw, img: Image.Image,
                          pal: dict, cx: int,
                          top_y: int, mid_y: int, bot_y: int,
                          wide: int, neck: int, progress: float):
    """
    Draw the filled hourglass with sand.
    progress 0.0 = full top / empty bottom
    progress 1.0 = empty top / full bottom
    """
    # --- glass shell (slightly wider outline) ---
    wall = 8
    glass_color = pal["glass"] + (120,)
    glass_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glass_img)

    for y in range(top_y, bot_y + 1):
        xl_inner, xr_inner = _hourglass_x_bounds(y, cx, top_y, mid_y, bot_y, wide, neck)
        xl_outer, xr_outer = (xl_inner - wall, xr_inner + wall)
        # Draw left wall stripe
        gdraw.line([(xl_outer, y), (xl_inner, y)], fill=glass_color)
        # Draw right wall stripe
        gdraw.line([(xr_inner, y), (xr_outer, y)], fill=glass_color)

    # Top and bottom caps
    gdraw.line([(cx - wide - wall, top_y), (cx + wide + wall, top_y)], fill=glass_color, width=wall)
    gdraw.line([(cx - wide - wall, bot_y), (cx + wide + wall, bot_y)], fill=glass_color, width=wall)

    img.paste(Image.alpha_composite(img.convert("RGBA"), glass_img).convert("RGB"))

    # --- sand in top half (draining) ---
    sand_top_end = top_y + int((1.0 - progress) * (mid_y - top_y))
    for y in range(top_y, mid_y):
        xl, xr = _hourglass_x_bounds(y, cx, top_y, mid_y, bot_y, wide, neck)
        if y <= sand_top_end:
            # sand fill
            t = (y - top_y) / max(sand_top_end - top_y, 1)
            color = lerp_color(pal["sand_hi"], pal["sand_lo"], t)
            draw.line([(xl + 1, y), (xr - 1, y)], fill=color)
        else:
            # empty glass interior (dark tint)
            draw.line([(xl + 1, y), (xr - 1, y)], fill=lerp_color(pal["bg"], pal["glass2"], 0.15))

    # --- sand in bottom half (filling) ---
    sand_bot_start = bot_y - int(progress * (bot_y - mid_y))
    for y in range(mid_y, bot_y + 1):
        xl, xr = _hourglass_x_bounds(y, cx, top_y, mid_y, bot_y, wide, neck)
        if y >= sand_bot_start:
            t = (y - sand_bot_start) / max(bot_y - sand_bot_start, 1)
            color = lerp_color(pal["sand_lo"], pal["sand_hi"], t * 0.6)
            draw.line([(xl + 1, y), (xr - 1, y)], fill=color)
        else:
            draw.line([(xl + 1, y), (xr - 1, y)], fill=lerp_color(pal["bg"], pal["glass2"], 0.15))

    # --- flowing sand stream in neck ---
    if 0.02 < progress < 0.98:
        stream_color = pal["sand_hi"]
        for y in range(mid_y - 20, mid_y + 30):
            xl, xr = _hourglass_x_bounds(y, cx, top_y, mid_y, bot_y, wide, neck)
            mid_x = (xl + xr) // 2
            w = max(1, (xr - xl) // 3)
            draw.line([(mid_x - w, y), (mid_x + w, y)], fill=stream_color)

    # --- glow effect around hourglass edges ---
    glow_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_img)
    gd.rectangle([cx - wide - wall - 2, top_y - 2, cx + wide + wall + 2, bot_y + 2],
                  outline=pal["glow"] + (60,), width=3)
    glow_blur = glow_img.filter(ImageFilter.GaussianBlur(radius=10))
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow_blur).convert("RGB"))


def _draw_sand_surface_sparkles(draw: ImageDraw.Draw, pal: dict,
                                 cx: int, top_y: int, mid_y: int, bot_y: int,
                                 wide: int, neck: int, progress: float, seed: int):
    """Draw tiny sparkle dots on the sand surface."""
    rng = random.Random(seed)
    sand_top_end = top_y + int((1.0 - progress) * (mid_y - top_y))
    sand_bot_start = bot_y - int(progress * (bot_y - mid_y))

    color = lerp_color(pal["sand_hi"], (255, 255, 255), 0.5)

    for _ in range(15):
        # Top sand sparkles
        sy = rng.randint(top_y, min(sand_top_end, mid_y - 5))
        xl, xr = _hourglass_x_bounds(sy, cx, top_y, mid_y, bot_y, wide, neck)
        sx = rng.randint(xl + 2, max(xl + 3, xr - 2))
        alpha = rng.randint(60, 180)
        draw.point((sx, sy), fill=color)

    for _ in range(15):
        # Bottom sand sparkles
        sy = rng.randint(max(sand_bot_start, mid_y + 5), bot_y - 2)
        xl, xr = _hourglass_x_bounds(sy, cx, top_y, mid_y, bot_y, wide, neck)
        sx = rng.randint(xl + 2, max(xl + 3, xr - 2))
        draw.point((sx, sy), fill=color)


def _get_font(size: int):
    """Try to load a nice font, fall back to default."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_text_overlay(draw: ImageDraw.Draw, pal: dict,
                        time_str: str, mode_name: str, phase: str,
                        session_count: int, sessions_before_long: int,
                        WIDTH: int, HEIGHT: int, bot_y: int):
    """Draw all text labels onto the image."""
    cx = WIDTH // 2

    # — Big timer countdown —
    font_time = _get_font(72)
    bbox = draw.textbbox((0, 0), time_str, font=font_time)
    tw = bbox[2] - bbox[0]
    tx = cx - tw // 2
    ty = bot_y + 30

    # Shadow
    draw.text((tx + 2, ty + 2), time_str, font=font_time, fill=(0, 0, 0))
    draw.text((tx, ty), time_str, font=font_time, fill=pal["text"])

    # — Phase label —
    font_label = _get_font(28)
    label_full = f"{pal['emoji']}  {pal['label']}"
    bbox2 = draw.textbbox((0, 0), label_full, font=font_label)
    lw = bbox2[2] - bbox2[0]
    draw.text((cx - lw // 2, ty + 88), label_full, font=font_label, fill=pal["sub"])

    # — Mode name (top of image) —
    font_mode = _get_font(22)
    bbox3 = draw.textbbox((0, 0), mode_name, font=font_mode)
    mw = bbox3[2] - bbox3[0]
    draw.text((cx - mw // 2, 18), mode_name, font=font_mode, fill=pal["sub"])

    # — Session dots —
    dot_y = HEIGHT - 38
    dot_r = 9
    spacing = 28
    total_dots = sessions_before_long
    start_x = cx - (total_dots - 1) * spacing // 2

    for i in range(total_dots):
        dot_cx = start_x + i * spacing
        filled = i < (session_count % sessions_before_long or
                      (sessions_before_long if session_count > 0 and session_count % sessions_before_long == 0 else 0))
        if filled:
            draw.ellipse([dot_cx - dot_r, dot_y - dot_r, dot_cx + dot_r, dot_y + dot_r],
                          fill=pal["glow"])
        else:
            draw.ellipse([dot_cx - dot_r, dot_y - dot_r, dot_cx + dot_r, dot_y + dot_r],
                          outline=pal["sub"], width=2)

    # — Session counter text —
    font_sess = _get_font(20)
    sess_text = f"Сессия #{session_count + 1}"
    bbox4 = draw.textbbox((0, 0), sess_text, font=font_sess)
    sw = bbox4[2] - bbox4[0]
    draw.text((cx - sw // 2, dot_y - 28), sess_text, font=font_sess, fill=pal["sub"])


def create_hourglass_image(
    progress: float,
    phase: str,
    time_str: str,
    mode_name: str,
    session_count: int,
    sessions_before_long: int,
    seed: int = 0,
) -> io.BytesIO:
    """
    Generate a hourglass image and return it as a BytesIO PNG buffer.

    Args:
        progress:            0.0 = timer just started, 1.0 = timer done
        phase:               "focus" | "short_break" | "long_break"
        time_str:            formatted time string, e.g. "23:45"
        mode_name:           display name of the mode, e.g. "🍅 Классическое"
        session_count:       number of completed focus sessions
        sessions_before_long: how many sessions before long break
        seed:                random seed for reproducible sparkle positions
    """
    pal = PALETTES.get(phase, PALETTES["focus"])
    progress = max(0.0, min(1.0, progress))

    img = Image.new("RGB", (WIDTH, HEIGHT), pal["bg"])
    _draw_gradient_bg(img, pal)
    if pal["star"]:
        _draw_stars(img, pal)

    draw = ImageDraw.Draw(img)

    # Hourglass geometry
    cx    = WIDTH // 2
    top_y = 100
    bot_y = HEIGHT - 210
    mid_y = (top_y + bot_y) // 2
    wide  = 175
    neck  = 16

    _draw_hourglass_body(draw, img, pal, cx, top_y, mid_y, bot_y, wide, neck, progress)
    _draw_sand_surface_sparkles(draw, pal, cx, top_y, mid_y, bot_y, wide, neck, progress, seed)
    _draw_text_overlay(draw, pal, time_str, mode_name, phase, session_count,
                       sessions_before_long, WIDTH, HEIGHT, bot_y)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
