#!/usr/bin/env python3
"""Regenerate BlockTV's app artwork from the authored SVG sources.

    python3 tools/build_assets.py [--check]

Renders with rsvg-convert (brew install librsvg) rather than a PNG export,
so the source of truth stays vector. Note that macOS `qlmanage` can also
render SVG but flattens the alpha channel, which is useless here.

Outputs, all indexed-palette PNG with a tRNS chunk — the MicroPythonOS
lodepng build silently fails to draw RGBA truecolour PNGs:

    icon_64x64.png                        launcher icon, TV mark + white halo
    res/drawable-mdpi/blocktv_logo_light.png   splash, dark ink  (light themes)
    res/drawable-mdpi/blocktv_logo_dark.png    splash, light ink (dark themes)

--check reports what would change without writing anything.
"""

import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops

ART = "/Users/RT/Documents/Projects/BlockTV"
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "org.zaptv.blocktv")

SOURCES = {
    "tv": os.path.join(ART, "blocktv-tv-logo-trans.svg"),
    "full_black": os.path.join(ART, "blocktv-logo-trans-black.svg"),
    "full_white": os.path.join(ART, "blocktv-logo-trans-white.svg"),
}

ICON_PX = 64
ICON_RENDER_PX = 1200      # render big, downscale once — keeps edges clean
# No synthetic halo: measured against ZapTV's icon, its white border is
# ~1 px at 64 px — just the artwork's own cream outline, which BlockTV's
# artwork also carries. The Gaussian halo previously added here came out
# 4-8 px thick and made the mark visibly smaller than ZapTV's.
# Sized to match the sibling ZapTV app so the two feel like one family.
# In both logos the TV mark spans the full asset height, so equal height
# = equal-sized TV on screen: ZapTV's splash is 96 px tall (mark = 40%
# of a 240 px screen). BlockTV's overall width then lands wherever its
# longer wordmark puts it. The icon mark fills the tile width at 89% of
# its height, ~1 px cream border from the artwork itself (no halo).
SPLASH_H = 96
SPLASH_RENDER_PX = 1900
ICON_FILL_W = 64 / 64.0
ICON_FILL_H = 57 / 64.0


def render(svg, width):
    """SVG -> RGBA image at the requested width."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run(["rsvg-convert", "-w", str(width), "-o", path, svg],
                       check=True, capture_output=True)
        return Image.open(path).convert("RGBA").copy()
    finally:
        os.unlink(path)


def to_indexed(img):
    # method=2 (FASTOCTREE) is the one that carries alpha into the palette.
    return img.quantize(colors=255, method=2, dither=Image.Dither.FLOYDSTEINBERG)


def build_icon():
    # The artwork as drawn — its own cream outline provides the dark-
    # launcher separation, exactly as ZapTV's does. Scaled so the mark is
    # the SAME HEIGHT as ZapTV's (57 of 64 px): BlockTV's artwork is a
    # slightly wider shape because of the flying shards, so matching
    # height means the outermost shard tips graze past the tile edge and
    # are cropped — the same way ZapTV's own mark touches its edges.
    art = render(SOURCES["tv"], ICON_RENDER_PX)
    art = art.crop(art.split()[3].getbbox())
    box_h = round(ICON_PX * ICON_FILL_H)
    scale = box_h / art.size[1]
    target = (max(1, round(art.size[0] * scale)), box_h)
    mark = art.resize(target, Image.LANCZOS)
    tile = Image.new("RGBA", (ICON_PX, ICON_PX), (255, 255, 255, 0))
    tile.alpha_composite(mark, ((ICON_PX - target[0]) // 2, (ICON_PX - target[1]) // 2))
    return to_indexed(tile)


def build_splash():
    """Both variants share one crop box so the logo does not shift or
    resize when the theme flips."""
    black = render(SOURCES["full_black"], SPLASH_RENDER_PX)
    white = render(SOURCES["full_white"], SPLASH_RENDER_PX)
    bb, wb = black.split()[3].getbbox(), white.split()[3].getbbox()
    box = (min(bb[0], wb[0]), min(bb[1], wb[1]), max(bb[2], wb[2]), max(bb[3], wb[3]))
    out = []
    for src in (black, white):
        im = src.crop(box)
        scale = SPLASH_H / im.size[1]
        im = im.resize((max(1, round(im.size[0] * scale)), SPLASH_H), Image.LANCZOS)
        out.append(to_indexed(im))
    return out           # light-theme asset, dark-theme asset


def write(img, relpath, check):
    path = os.path.normpath(os.path.join(APP, relpath))
    new = img.convert("RGBA")
    if os.path.exists(path):
        old = Image.open(path).convert("RGBA")
        if old.size == new.size and ImageChops.difference(old, new).getbbox() is None:
            print("  unchanged  %s" % relpath)
            return False
    if check:
        print("  WOULD WRITE %s %s" % (relpath, new.size))
        return True
    img.save(path, optimize=True)
    print("  wrote      %s %s (%d bytes)" % (relpath, img.size, os.stat(path).st_size))
    return True


def main():
    check = "--check" in sys.argv
    for name, path in SOURCES.items():
        if not os.path.exists(path):
            sys.exit("missing source: %s" % path)
    try:
        subprocess.run(["rsvg-convert", "--version"], check=True, capture_output=True)
    except Exception:
        sys.exit("rsvg-convert not found — run: brew install librsvg")

    print("building from %s" % ART)
    changed = write(build_icon(), "icon_64x64.png", check)
    light, dark = build_splash()
    changed |= write(light, "res/drawable-mdpi/blocktv_logo_light.png", check)
    changed |= write(dark, "res/drawable-mdpi/blocktv_logo_dark.png", check)
    print("done —", "changes pending" if (check and changed) else
          ("updated" if changed else "everything already current"))


if __name__ == "__main__":
    main()
