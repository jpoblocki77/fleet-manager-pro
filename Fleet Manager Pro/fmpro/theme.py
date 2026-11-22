"""Company colours: swap the navy blue and crimson red for any two colours (Settings -> Company).

Every shade in static/css/app.css is a CSS variable. From the two chosen colours this works out the whole palette,
keeping each shade's place in the design: the backgrounds stay as dark (or, in the light theme, as light) as before so
text stays readable, and they only take on the new colour's hue. With nothing chosen no CSS is added at all, so the
app looks exactly as designed.
"""
import colorsys
import re

DEFAULT_NAVY = "#0a1a33"
DEFAULT_ACCENT = "#c8102e"
HEX = re.compile(r"#[0-9a-fA-F]{6}")

# the shades worked out from the "navy" colour: (css variable, its colour in the built-in palette)
NAVY_DARK = (("--navy-950", "#050e1c"), ("--navy-800", "#0f2546"), ("--navy-700", "#15325e"),
             ("--navy-600", "#1d4278"), ("--navy-500", "#2a5592"), ("--navy-300", "#8fa8cc"), ("--navy-100", "#dbe5f3"),
             ("--bg", "#071327"), ("--panel", "#0d203d"), ("--panel-2", "#112a4f"), ("--line", "#1d3a66"),
             ("--line-soft", "#17305a"), ("--muted", "#93a7c6"), ("--faint", "#5f789d"))
NAVY_LIGHT = (("--bg", "#eef2f8"), ("--panel-2", "#f4f7fc"), ("--line", "#d3dcea"), ("--line-soft", "#e3e9f3"),
              ("--muted", "#4c6283"), ("--faint", "#8a9bb5"))
ACCENT = (("--crimson-600", "#a50d26"), ("--crimson-400", "#e23552"), ("--crimson-100", "#fbe3e7"))
SIDEBAR = ("--navy-950", "--navy-800", "--navy-700")
NAVY_DARKEST, NAVY_LIGHTEST = 0.03, 0.30      # how dark / light the sidebar colour itself may be and stay readable


def _hls(hexc):
    return colorsys.rgb_to_hls(*(int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)))


def _hex(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, min(1.0, max(0.0, l)), min(1.0, max(0.0, s)))
    return "#" + "".join(f"{round(x * 255):02x}" for x in (r, g, b))


def _follow(shade, base, chosen, shift_light=True):
    """`shade` relates to `base` in the built-in palette; give it the same relation to `chosen`."""
    hd, ld, sd = _hls(shade)
    hb, lb, sb = _hls(base)
    hc, lc, sc = chosen
    s = sd * (sc / sb) if sb else sc
    return _hex(hd + (hc - hb), ld + ((lc - lb) if shift_light else 0), s)


def clean(value):
    """A #rrggbb colour, or '' for anything else (an empty setting means 'the built-in colour')."""
    v = (value or "").strip()
    return v.lower() if HEX.fullmatch(v) else ""


def on_colour(hexc):
    """Black or white text, whichever reads better on this background."""
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#fff" if (1.05 / (lum + 0.05)) >= ((lum + 0.05) / 0.05) else "#000"


def palette(navy="", accent=""):
    """(dark-theme variables, light-theme variables) for the chosen colours; empty dicts for the built-in ones."""
    navy, accent = clean(navy), clean(accent)
    dark, light = {}, {}
    if navy and navy != DEFAULT_NAVY:
        h, l, s = _hls(navy)
        l = min(NAVY_LIGHTEST, max(NAVY_DARKEST, l))
        base = _hex(h, l, s)
        dark["--navy-900"] = base
        for var, shade in NAVY_DARK:   # the sidebar shades follow its brightness; page backgrounds keep theirs
            dark[var] = _follow(shade, DEFAULT_NAVY, (h, l, s), shift_light=var in SIDEBAR)
        for var, shade in NAVY_LIGHT:
            light[var] = _follow(shade, DEFAULT_NAVY, (h, l, s), shift_light=False)
        light["--text"] = base
    if accent and accent != DEFAULT_ACCENT:
        chosen = _hls(accent)
        dark["--crimson"] = accent
        for var, shade in ACCENT:
            dark[var] = _follow(shade, DEFAULT_ACCENT, chosen)
        dark["--on-accent"] = on_colour(accent)
    return dark, light


def css(navy="", accent=""):
    """The <style> text that applies the chosen colours on top of app.css ('' when both are the built-in ones).
    Uses no quotes or angle brackets, so the page template can keep escaping it as normal text."""
    dark, light = palette(navy, accent)
    out = ""
    if dark:
        out += ":root{" + "".join(f"{k}:{v};" for k, v in dark.items()) + "}"
    if light:
        out += ":root[data-theme=light]{" + "".join(f"{k}:{v};" for k, v in light.items()) + "}"
    return out


def from_settings(settings):
    return css(settings.get("theme_navy", ""), settings.get("theme_accent", ""))
