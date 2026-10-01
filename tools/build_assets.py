#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of BlockTV. BlockTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""Regenerate BlockTV's logo assets from the SVG sources in artwork/.

    python3 tools/build_assets.py [--check]

Sources are the ZapTV family files, copied into artwork/ with their C2PA
<metadata> block stripped and nothing else changed:

    artwork/blocktv-logo.svg       the TV mark (viewBox 0 0 634 567)
    artwork/blocktv-wordmark.svg   the BLOCKTV wordmark: #111 ink over a
                                   cream #f8f5eb halo, so it reads on light
                                   and dark backgrounds alike

Outputs, all indexed-palette PNG with a tRNS chunk (older MicroPythonOS
lodepng builds silently fail to draw RGBA truecolour PNGs):

    icon_64x64.png                             launcher icon
    res/drawable-mdpi/blocktv_logo_light.png   splash and About lockup
    res/drawable-mdpi/blocktv_logo_dark.png    the same lockup

The launcher icon is the whole mark viewBox fitted to the tile width
(64x57) and centred vertically (y=3) in a fully transparent tile, the
convention the whole ZapTV family follows.

The lockup is composed here from the two sources: the mark on the left,
the wordmark on the right, mark height 2.2x the wordmark's viewBox height,
a gap of 0.35x that height, and the wordmark centred vertically on the
mark. The light and dark files hold the SAME artwork: the cream halo lets
one design work on both themes, and keeping both names means the app's
theme switch needs no change.

Renders with rsvg-convert (brew install librsvg), which ignores the mark's
CSS animation and draws its resting frame. Everything is rendered large
and downscaled once with LANCZOS, which keeps the edges clean. macOS
`qlmanage` can also render SVG but flattens the alpha channel, so it is no
use here.

--check reports what would change without writing anything.
"""

import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ART = os.path.join(ROOT, "artwork")
APP = os.path.join(ROOT, "org.zaptv.blocktv")

SOURCES = {
    "logo": os.path.join(ART, "blocktv-logo.svg"),
    "wordmark": os.path.join(ART, "blocktv-wordmark.svg"),
}

ICON_PX = 64
ICON_RENDER_PX = 1200        # render big, downscale once

# Same width as the splash it replaces, so the About page (whose logo
# scale was tuned for that width) still fits; the height follows from the
# lockup's proportions (276x75).
LOCKUP_W = 276
LOCKUP_RENDER_PX = 8 * LOCKUP_W
LOCKUP_MARK_RATIO = 2.2      # mark height / wordmark viewBox height
LOCKUP_GAP_RATIO = 0.35      # gap / wordmark viewBox height


def read_svg(path):
    """(viewBox as four floats, markup inside the root <svg>) of a file."""
    with open(path, encoding="utf-8") as f:
        s = f.read()
    s = re.sub(r"<metadata>.*?</metadata>", "", s, flags=re.S)
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', s).group(1).split()]
    body = s[s.index(">", s.index("<svg")) + 1:s.rindex("</svg>")]
    return vb, body


def render(svg_text, width):
    """SVG markup -> RGBA image at the requested width."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.svg")
        out = os.path.join(tmp, "out.png")
        with open(src, "w", encoding="utf-8") as f:
            f.write(svg_text)
        subprocess.run(["rsvg-convert", "-w", str(width), "-o", out, src],
                       check=True, capture_output=True)
        return Image.open(out).convert("RGBA").copy()


def to_indexed(img):
    # method=2 (FASTOCTREE) is the one that carries alpha into the palette.
    return img.quantize(colors=255, method=2, dither=Image.Dither.FLOYDSTEINBERG)


def build_icon():
    vb, _ = read_svg(SOURCES["logo"])
    with open(SOURCES["logo"], encoding="utf-8") as f:
        art = render(f.read(), ICON_RENDER_PX)
    h = round(ICON_PX * vb[3] / vb[2])                   # 57
    mark = art.resize((ICON_PX, h), Image.LANCZOS)
    tile = Image.new("RGBA", (ICON_PX, ICON_PX), (255, 255, 255, 0))
    tile.alpha_composite(mark, (0, (ICON_PX - h) // 2))  # y = 3
    return to_indexed(tile)


def lockup_svg():
    """The horizontal lockup as one SVG, both sources nested whole."""
    lvb, lbody = read_svg(SOURCES["logo"])
    wvb, wbody = read_svg(SOURCES["wordmark"])
    ww, wh = wvb[2], wvb[3]
    mh = LOCKUP_MARK_RATIO * wh
    mw = mh * lvb[2] / lvb[3]
    gap = LOCKUP_GAP_RATIO * wh

    def nest(x, y, w, h, vb, body):
        return ('<svg x="%.3f" y="%.3f" width="%.3f" height="%.3f" viewBox="%s">%s</svg>'
                % (x, y, w, h, " ".join("%g" % v for v in vb), body))

    w, h = mw + gap + ww, mh
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.3f %.3f">' % (w, h)
           + nest(0, 0, mw, mh, lvb, lbody)
           + nest(mw + gap, (mh - wh) / 2, ww, wh, wvb, wbody)
           + "</svg>")
    return svg, w, h


def build_lockup():
    svg, w, h = lockup_svg()
    art = render(svg, LOCKUP_RENDER_PX)
    size = (LOCKUP_W, round(LOCKUP_W * h / w))           # 276x75
    return to_indexed(art.resize(size, Image.LANCZOS))


def write(img, relpath, check):
    path = os.path.normpath(os.path.join(APP, relpath))
    new = img.convert("RGBA")
    if os.path.exists(path):
        old = Image.open(path)
        if (old.mode == img.mode and old.size == new.size
                and ImageChops.difference(old.convert("RGBA"), new).getbbox() is None):
            print("  unchanged  %s" % relpath)
            return False
    if check:
        print("  WOULD WRITE %s %s %s" % (relpath, img.mode, new.size))
        return True
    img.save(path, optimize=True)
    print("  wrote      %s %s %s (%d bytes)"
          % (relpath, img.mode, img.size, os.stat(path).st_size))
    return True


def main():
    check = "--check" in sys.argv
    for path in SOURCES.values():
        if not os.path.exists(path):
            sys.exit("missing source: %s" % path)
    try:
        subprocess.run(["rsvg-convert", "--version"], check=True, capture_output=True)
    except Exception:
        sys.exit("rsvg-convert not found; run: brew install librsvg")

    print("building from %s" % os.path.relpath(ART, ROOT))
    changed = write(build_icon(), "icon_64x64.png", check)
    lockup = build_lockup()
    changed |= write(lockup, "res/drawable-mdpi/blocktv_logo_light.png", check)
    changed |= write(lockup, "res/drawable-mdpi/blocktv_logo_dark.png", check)
    print("done:", "changes pending" if (check and changed) else
          ("updated" if changed else "everything already current"))


if __name__ == "__main__":
    main()
