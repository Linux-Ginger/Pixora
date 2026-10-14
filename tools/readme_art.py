#!/usr/bin/env python3
"""Generate the SVG cards used by README.md (assets/readme/*.svg).

Edit the texts below and run:  python3 tools/readme_art.py
The SVGs are self-contained (no external fonts or images), so GitHub shows
them as-is in both its light and dark theme.
"""

import os
from xml.sax.saxutils import escape

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "readme")

# ── Palette ────────────────────────────────────────────────────────────
ORANGE = "#e95420"      # Pixora's accent (same as the app)
CARD = "#1d1d20"
CARD_EDGE = "#2e2e33"
INSET = "#151517"
INSET_EDGE = "#2a2a2e"
TEXT = "#f3f3f4"
DIM = "#9b9ba3"
FONT = "Ubuntu, Cantarell, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

# The Pixora flower (assets/logos/pixora-icon.svg), inlined.
PETALS = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#a855f7"]


def logo(cx, cy, size):
    s = size / 120.0
    g = [f'<g transform="translate({cx},{cy}) scale({s:.4f})">']
    for i, col in enumerate(PETALS):
        g.append(f'<ellipse cx="0" cy="-30" rx="18" ry="28" fill="{col}" '
                 f'opacity="0.95" transform="rotate({i * 60})"/>')
    g.append('<circle r="13" fill="#1e1e1e"/>'
             '<circle r="8" fill="none" stroke="#fff" stroke-width="1.2" opacity="0.3"/>'
             '<circle r="3" fill="#fff" opacity="0.6"/></g>')
    return "".join(g)


# ── Icons (24×24, stroke) ──────────────────────────────────────────────
ICONS = {
    "phone": '<rect x="5" y="2" width="14" height="20" rx="2.5"/><path d="M11 18h2"/>',
    "live": '<circle cx="12" cy="12" r="2.5"/><circle cx="12" cy="12" r="6"/>'
            '<circle cx="12" cy="12" r="10" stroke-dasharray="1.6 2.4"/>',
    "sparkles": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>'
                '<path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/>'
            '<path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9'
              'A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
    "convert": '<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/>'
               '<path d="M20 17H4"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
             '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    "pin": '<path d="M20 10c0 5-5.5 10.2-7.4 11.8a1 1 0 0 1-1.2 0C9.5 20.2 4 15 4 10a8 8 0 0 1 16 0"/>'
           '<circle cx="12" cy="10" r="3"/>',
    "drive": '<path d="M22 12H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6'
             'l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/><path d="M6 16h.01M10 16h.01"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.8 9.8 0 0 1 6.7 2.7L21 8"/><path d="M21 3v5h-5"/>'
               '<path d="M21 12a9 9 0 0 1-9 9 9.8 9.8 0 0 1-6.7-2.7L3 16"/><path d="M8 16H3v5"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
             '<path d="M2 12h20"/>',
    "heart": '<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2'
             'A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4'
            'M2 9h4M2 15h4M18 9h4M18 15h4"/><path d="M10 10h4v4h-4z"/>',
}


def icon(name, x, y, size=26, color=ORANGE, width=2):
    s = size / 24.0
    return (f'<g transform="translate({x},{y}) scale({s:.4f})" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">'
            f'{ICONS[name]}</g>')


def text(x, y, s, size=14, color=TEXT, weight=400, anchor="start", spacing=None):
    sp = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}"{sp}>'
            f'{escape(s)}</text>')


def lines(x, y, rows, size=13, color=DIM, lh=1.45, **kw):
    return "".join(text(x, y + i * size * lh, r, size, color, **kw) for i, r in enumerate(rows))


def card(x, y, w, h, r=14, fill=CARD, edge=CARD_EDGE):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
            f'stroke="{edge}" stroke-width="1"/>')


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title)}">'
            f'<title>{escape(title)}</title>'
            '<defs><filter id="sh" x="-10%" y="-10%" width="120%" height="130%">'
            '<feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000" flood-opacity="0.28"/>'
            '</filter></defs>'
            f'{body}</svg>')


def pill(x, y, w, label, dot=None, ic=None):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="30" rx="15" fill="#2a2a2f" stroke="#3a3a40"/>']
    tx = x + 16
    if dot:
        out.append(f'<circle cx="{x + 16}" cy="{y + 15}" r="4" fill="{dot}"/>')
        tx = x + 28
    if ic:
        out.append(icon(ic, x + 10, y + 7, 16))
        tx = x + 32
    out.append(text(tx, y + 20, label, 12.5, TEXT, 600))
    return "".join(out)


# ── Banner ─────────────────────────────────────────────────────────────
def banner():
    W, H = 880, 290
    ch = H - 26
    b = [f'<clipPath id="bc"><rect x="10" y="6" width="{W - 20}" height="{ch}" rx="18"/></clipPath>',
         f'<g filter="url(#sh)">{card(10, 6, W - 20, ch, 18)}</g>',
         f'<rect x="10" y="6" width="{W - 20}" height="5" fill="{ORANGE}" clip-path="url(#bc)"/>',
         # Logo disc
         f'<circle cx="150" cy="138" r="86" fill="{INSET}" stroke="{INSET_EDGE}"/>',
         logo(150, 138, 140),
         # Brand line
         logo(290, 56, 22), text(308, 63, "Pixora", 18, TEXT, 700),
         text(282, 120, "Your photos,", 44, TEXT, 800),
         text(282, 168, "kept tidy", 44, ORANGE, 800),
         text(282, 200, "Import from your iPhone, sort, browse and back up — offline, on your own PC.",
              13.5, DIM),
         pill(282, 218, 132, "Version 1.68", dot="#22c55e"),
         pill(424, 218, 168, "Free & open source", ic="heart"),
         pill(602, 218, 190, "Ubuntu 24.04 and newer", dot=ORANGE)]
    return svg(W, H, "".join(b), "Pixora — your photos, kept tidy")


# ── How it works ───────────────────────────────────────────────────────
def how_it_works():
    cw, ch, gap = 266, 270, 40
    W, H = 20 + 3 * cw + 2 * gap, 300
    steps = [
        ("Connect your iPhone", ["Plug it in, unlock it and tap", "Trust. Pixora takes it from", "there."]),
        ("Pick and import", ["Choose photos and videos.", "Possible duplicates are shown", "side by side first."]),
        ("Enjoy, safely", ["Filed by date, on the map,", "and backed up to your USB", "drive after every import."]),
    ]
    b = []
    for i, (title, desc) in enumerate(steps):
        x = 10 + i * (cw + gap)
        y = 10
        b.append(f'<g filter="url(#sh)">{card(x, y, cw, ch)}</g>')
        ix, iy, iw, ih = x + 14, y + 14, cw - 28, 128
        b.append(card(ix, iy, iw, ih, 10, INSET, INSET_EDGE))
        b.append(_illustration(i, ix, iy, iw, ih))
        b.append(f'<circle cx="{x + 30}" cy="{y + 172}" r="13" fill="{ORANGE}"/>')
        b.append(text(x + 30, y + 177, str(i + 1), 14, "#fff", 800, "middle"))
        b.append(text(x + 52, y + 178, title, 15.5, TEXT, 700))
        b.append(lines(x + 18, y + 208, desc, 12.5))
        if i < 2:
            ax = x + cw + 8
            b.append(f'<path d="M{ax} {y + 85}h22m-7-7 7 7-7 7" fill="none" stroke="{ORANGE}" '
                     'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>')
    return svg(W, H, "".join(b), "How Pixora works: connect, pick, enjoy")


def _illustration(i, x, y, w, h):
    cx = x + w / 2
    out = []
    if i == 0:
        # A phone showing the Trust prompt.
        out.append(f'<rect x="{cx - 46}" y="{y + 10}" width="92" height="108" rx="14" '
                   f'fill="#26262b" stroke="#3b3b42" stroke-width="2"/>')
        out.append(f'<rect x="{cx - 38}" y="{y + 28}" width="76" height="66" rx="9" fill="#f2f2f4"/>')
        out.append(text(cx, y + 45, "Trust this", 9.5, "#222", 700, "middle"))
        out.append(text(cx, y + 57, "computer?", 9.5, "#222", 700, "middle"))
        out.append(f'<rect x="{cx - 32}" y="{y + 65}" width="64" height="11" rx="5.5" fill="{ORANGE}"/>')
        out.append(text(cx, y + 73.5, "Trust", 7.5, "#fff", 700, "middle"))
        out.append(f'<rect x="{cx - 32}" y="{y + 79}" width="64" height="11" rx="5.5" fill="#d6d6da"/>')
        out.append(text(cx, y + 87.5, "Don't Trust", 7.5, "#333", 700, "middle"))
        out.append(f'<path d="M{cx - 6} {y + 108}h12" stroke="#55555c" stroke-width="2" '
                   'stroke-linecap="round"/>')
        # USB cable into the computer
        out.append(f'<path d="M{cx} {y + 118}v10" stroke="#55555c" stroke-width="3"/>')
    elif i == 1:
        cols = ["#3b82f6", "#22c55e", "#eab308", "#ef4444", "#a855f7", "#f97316"]
        for k, col in enumerate(cols):
            gx = x + 30 + (k % 3) * 66
            gy = y + 16 + (k // 3) * 54
            out.append(f'<rect x="{gx}" y="{gy}" width="56" height="44" rx="6" fill="{col}" opacity="0.85"/>')
            if k != 4:
                out.append(f'<circle cx="{gx + 47}" cy="{gy + 9}" r="7" fill="{ORANGE}" stroke="#fff" stroke-width="1.5"/>'
                           f'<path d="M{gx + 43.5} {gy + 9}l2.4 2.4 4.2-4.6" fill="none" stroke="#fff" '
                           'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
        out.append(f'<rect x="{x + 96}" y="{y + 66}" width="56" height="16" rx="8" fill="#111" opacity="0.8"/>')
        out.append(text(x + 124, y + 78, "duplicate?", 8.5, "#fff", 700, "middle"))
    else:
        # Map with pins, plus a backup drive.
        out.append(f'<rect x="{x + 18}" y="{y + 16}" width="140" height="96" rx="8" fill="#203026"/>')
        out.append(f'<path d="M{x + 18} {y + 70}c30-14 60 10 90-8s40-6 50-14" stroke="#2f6f9f" '
                   'stroke-width="7" fill="none" opacity="0.8"/>')
        out.append(f'<path d="M{x + 60} {y + 16}v96M{x + 18} {y + 46}h140" stroke="#2c4434" stroke-width="1.5"/>')
        for px, py in ((x + 52, y + 50), (x + 104, y + 38), (x + 130, y + 86)):
            out.append(f'<circle cx="{px}" cy="{py}" r="8" fill="{ORANGE}" stroke="#fff" stroke-width="2"/>')
        out.append(icon("drive", x + 176, y + 46, 46, "#d6d6da", 1.6))
        out.append(f'<circle cx="{x + 213}" cy="{y + 50}" r="9" fill="#22c55e"/>'
                   f'<path d="M{x + 209} {y + 50}l3 3 5-6" fill="none" stroke="#fff" stroke-width="2" '
                   'stroke-linecap="round" stroke-linejoin="round"/>')
    return "".join(out)


# ── Features ───────────────────────────────────────────────────────────
FEATURES = [
    ("phone", "Plug in & import", ["Spots your iPhone or iPad,", "waits for Trust and imports", "what you pick."]),
    ("live", "Live Photos", ["The photo and its short", "video come over together.", "Press LIVE to play it."]),
    ("sparkles", "Keeps your edits", ["Crops and filters you made", "on the phone arrive exactly", "as you see them there."]),
    ("copy", "Real duplicate check", ["Compares the pictures", "themselves, not file names.", "You decide per photo."]),
    ("folder", "Tidy folders", ["Flat, per year, or per year", "and month — and Pixora", "keeps it that way."]),
    ("convert", "Opens everywhere", ["Optional HEIC → JPEG and", "MOV → MP4, with date and", "location kept."]),
    ("image", "Fast viewer", ["Grid by date, filmstrip,", "zoom, and videos with", "frame previews."]),
    ("crop", "Built-in editor", ["Rotate, crop and filters.", "The original is kept, so you", "can always undo."]),
    ("pin", "Map & places", ["See where photos were", "taken. Mark home and the", "places that matter."]),
    ("drive", "Backup to USB", ["After every import a copy", "goes to your USB drive —", "backup or exact mirror."]),
    ("undo", "Nothing lost by accident", ["Deleting goes to the trash.", "Sync stops if your photo", "folder is missing."]),
    ("refresh", "Updates itself", ["Checks for new releases", "and installs them for you,", "in one click."]),
]


def features():
    cw, ch, gx, gy = 274, 158, 19, 19
    W = 10 * 2 + 3 * cw + 2 * gx
    rows = (len(FEATURES) + 2) // 3
    H = 10 * 2 + rows * ch + (rows - 1) * gy + 8
    b = []
    for k, (ic, title, desc) in enumerate(FEATURES):
        x = 10 + (k % 3) * (cw + gx)
        y = 10 + (k // 3) * (ch + gy)
        b.append(f'<g filter="url(#sh)">{card(x, y, cw, ch)}</g>')
        b.append(icon(ic, x + 20, y + 20, 26))
        b.append(text(x + 20, y + 76, title, 15.5, TEXT, 700))
        b.append(lines(x + 20, y + 100, desc, 12.5))
    return svg(W, H, "".join(b), "What Pixora can do")


# ── Languages ──────────────────────────────────────────────────────────
def _flag(name, x, y, w=26, h=18):
    clip = f'<clipPath id="f{name}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3"/></clipPath>'
    g = [clip, f'<g clip-path="url(#f{name})">']
    if name == "en":
        g.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#012169"/>')
        g.append(f'<path d="M{x} {y}L{x + w} {y + h}M{x + w} {y}L{x} {y + h}" stroke="#fff" stroke-width="4"/>')
        g.append(f'<path d="M{x} {y}L{x + w} {y + h}M{x + w} {y}L{x} {y + h}" stroke="#c8102e" stroke-width="1.5"/>')
        g.append(f'<path d="M{x + w / 2} {y}v{h}M{x} {y + h / 2}h{w}" stroke="#fff" stroke-width="6"/>')
        g.append(f'<path d="M{x + w / 2} {y}v{h}M{x} {y + h / 2}h{w}" stroke="#c8102e" stroke-width="3.4"/>')
    else:
        bands = {"nl": ["#ae1c28", "#ffffff", "#21468b"],
                 "de": ["#000000", "#dd0000", "#ffce00"]}.get(name)
        if bands:
            for k, col in enumerate(bands):
                g.append(f'<rect x="{x}" y="{y + k * h / 3}" width="{w}" height="{h / 3 + 0.5}" fill="{col}"/>')
        else:  # fr: vertical bands
            for k, col in enumerate(["#002395", "#ffffff", "#ed2939"]):
                g.append(f'<rect x="{x + k * w / 3}" y="{y}" width="{w / 3 + 0.5}" height="{h}" fill="{col}"/>')
    g.append('</g>')
    g.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="none" stroke="#000" '
             'stroke-opacity="0.25"/>')
    return "".join(g)


def languages():
    W, H = 880, 160
    b = [f'<g filter="url(#sh)">{card(10, 6, W - 20, H - 22, 16)}</g>',
         icon("globe", 34, 30, 26),
         text(74, 42, "Speaks your language", 16, TEXT, 700),
         text(74, 62, "Picked automatically from your computer. You can always switch in Settings.", 12.5, DIM)]
    langs = [("en", "English"), ("nl", "Nederlands"), ("de", "Deutsch"), ("fr", "Français")]
    pw, gap = 196, 12
    for k, (code, name) in enumerate(langs):
        x = 30 + k * (pw + gap)
        y = 82
        b.append(f'<rect x="{x}" y="{y}" width="{pw}" height="40" rx="10" fill="#2a2a2f" stroke="#3a3a40"/>')
        b.append(_flag(code, x + 14, y + 11))
        b.append(text(x + 52, y + 25, name, 14, TEXT, 600))
    return svg(W, H, "".join(b), "Languages: English, Nederlands, Deutsch, Français")


# ── Credits ────────────────────────────────────────────────────────────
def credits():
    W, H = 880, 196
    b = [f'<g filter="url(#sh)">{card(10, 6, W - 20, H - 22, 16)}</g>',
         text(34, 38, "MADE BY", 11.5, DIM, 700, spacing="1.6")]
    cw = 374
    people = [
        (34, "Linux Ginger", ["The ideas, the choices and all the testing.", "Pixora is their project."], "lg"),
        (472, "Claude", ["Most of the code was written with the", "help of Claude, Anthropic's AI model."], "ai"),
    ]
    for x, name, desc, kind in people:
        b.append(card(x, 54, cw, 104, 12, INSET, INSET_EDGE))
        if kind == "lg":
            b.append(f'<circle cx="{x + 46}" cy="106" r="28" fill="{ORANGE}"/>')
            b.append(text(x + 46, 114, "LG", 20, "#fff", 800, "middle"))
        else:
            b.append(f'<circle cx="{x + 46}" cy="106" r="28" fill="#2a2a2f" stroke="#3a3a40"/>')
            b.append(icon("chip", x + 30, 90, 32, "#d6d6da", 1.6))
        b.append(text(x + 90, 92, name, 16, TEXT, 700))
        b.append(lines(x + 90, 116, desc, 12.5))
    b.append(f'<circle cx="440" cy="106" r="15" fill="{ORANGE}"/>')
    b.append(icon("plus", 431, 97, 18, "#fff", 2.6))
    return svg(W, H, "".join(b), "Made by Linux Ginger together with Claude")


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in [("banner", banner), ("how-it-works", how_it_works),
                     ("features", features), ("languages", languages),
                     ("credits", credits)]:
        with open(os.path.join(OUT, name + ".svg"), "w", encoding="utf-8") as f:
            f.write(fn())
        print("wrote", name + ".svg")


if __name__ == "__main__":
    main()
