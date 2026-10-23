"""Colours of the key buttons on the shortcut page: free (green), yours
(yellow), Omarchy (orange) and error (red).

The theme's own colours are used while they differ. Some themes make them
nearly one colour (Hackerman: four greens; several others: yellow = orange),
then all four take fixed hues, readable on the theme's background."""
import colorsys

# Green, yellow, orange and red, far enough apart to tell the keys apart.
KEY_OWNER_HUES = (0.33, 0.14, 0.065, 0.99)
MIN_KEY_HUE_DISTANCE = 0.07
# Key letters are small text on the card.
MIN_KEY_CONTRAST = 3.0


def rgb(hex_code):
    """(r, g, b) in 0..1 from "#rrggbb" or "#rgb", or None."""
    if not isinstance(hex_code, str):
        return None
    h = hex_code.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


def to_hex(color):
    return "#" + "".join(f"{round(v * 255):02x}" for v in color)


def hue(hex_code):
    color = rgb(hex_code)
    return colorsys.rgb_to_hsv(*color)[0] if color else 0.0


def hue_distance(a, b):
    distance = abs(hue(a) - hue(b))
    return min(distance, 1 - distance)


def luminance(color):
    """WCAG relative luminance."""
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def with_hue(hex_code, new_hue, background):
    """hex_code turned to new_hue at its brightness (made more colourful),
    then darker or lighter, whichever needs less, until it reads on
    background. Without a way to reach it, the best contrast found."""
    color, bg = rgb(hex_code), rgb(background) or (0.0, 0.0, 0.0)
    _, sat, val = colorsys.rgb_to_hsv(*color) if color else (0.0, 0.0, 0.75)
    sat, val = max(sat, 0.6), max(val, 0.7)
    best, best_contrast = None, -1.0
    # Values from the start outwards, nearer ones first, both directions.
    for step in range(51):
        for v in {min(1.0, val + step * 0.02), max(0.0, val - step * 0.02)}:
            out = to_hex(colorsys.hsv_to_rgb(new_hue, sat, v))
            ratio = contrast(rgb(out), bg)  # Measured on the rounded colour.
            if ratio >= MIN_KEY_CONTRAST:
                return out
            if ratio > best_contrast:
                best, best_contrast = out, ratio
    return best


def key_owner_colors(colors):
    """(free, yours, Omarchy, error) colours for a theme palette."""
    picked = [colors.get("green"), colors.get("yellow"), colors.get("orange"), colors.get("red")]
    if (not all(rgb(c) for c in picked)
            or any(hue_distance(a, b) < MIN_KEY_HUE_DISTANCE
                   for i, a in enumerate(picked) for b in picked[i + 1:])):
        picked = [with_hue(c, h, colors.get("background")) for c, h in zip(picked, KEY_OWNER_HUES)]
    return tuple(picked)
