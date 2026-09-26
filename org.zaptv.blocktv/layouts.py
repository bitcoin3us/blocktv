# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of BlockTV. BlockTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""Screen layouts: how a screen's fields are arranged, by field count.

A layout is a name and a small grid with a cell per field. Cells are
(col, row, colspan, rowspan) -- or just (col, row) for a single cell --
in slot order: field 1 of the screen fills slot 1, and so on, so the
order the user gives the fields in the editor is what assigns them to
slots. Where a layout has one bigger cell, that is slot 1: the field the
user lists first is the one that gets the room.

The first layout for each count is the arrangement the app always used
before layouts were a choice, so a screen with no layout set looks as it
always did.
"""

LAYOUTS = {
    1: (
        ("Full", 1, 1, ((0, 0),)),
    ),
    2: (
        ("Stacked", 1, 2, ((0, 0), (0, 1))),
        ("Side by side", 2, 1, ((0, 0), (1, 0))),
    ),
    3: (
        ("Two over wide", 2, 2, ((0, 0), (1, 0), (0, 1, 2, 1))),
        ("Wide over two", 2, 2, ((0, 0, 2, 1), (0, 1), (1, 1))),
        ("Big left", 2, 2, ((0, 0, 1, 2), (1, 0), (1, 1))),
        ("Big right", 2, 2, ((1, 0, 1, 2), (0, 0), (0, 1))),
        ("Stacked", 1, 3, ((0, 0), (0, 1), (0, 2))),
        ("Columns", 3, 1, ((0, 0), (1, 0), (2, 0))),
    ),
    4: (
        ("Grid", 2, 2, ((0, 0), (1, 0), (0, 1), (1, 1))),
        ("Big left", 2, 3, ((0, 0, 1, 3), (1, 0), (1, 1), (1, 2))),
        ("Big right", 2, 3, ((1, 0, 1, 3), (0, 0), (0, 1), (0, 2))),
        ("Wide over three", 3, 2, ((0, 0, 3, 1), (0, 1), (1, 1), (2, 1))),
        ("Three over wide", 3, 2, ((0, 0), (1, 0), (2, 0), (0, 1, 3, 1))),
        ("Stacked", 1, 4, ((0, 0), (0, 1), (0, 2), (0, 3))),
        ("Columns", 4, 1, ((0, 0), (1, 0), (2, 0), (3, 0))),
    ),
    5: (
        ("Two, two, wide", 2, 3, ((0, 0), (1, 0), (0, 1), (1, 1), (0, 2, 2, 1))),
        ("Wide over four", 2, 3, ((0, 0, 2, 1), (0, 1), (1, 1), (0, 2), (1, 2))),
        ("Big left", 2, 4, ((0, 0, 1, 4), (1, 0), (1, 1), (1, 2), (1, 3))),
        ("Big right", 2, 4, ((1, 0, 1, 4), (0, 0), (0, 1), (0, 2), (0, 3))),
        ("Stacked", 1, 5, ((0, 0), (0, 1), (0, 2), (0, 3), (0, 4))),
    ),
    6: (
        ("Grid, two wide", 2, 3, ((0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2))),
        ("Grid, three wide", 3, 2, ((0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1))),
        ("Big left", 3, 2, ((0, 0, 1, 2), (1, 0), (2, 0), (1, 1), (2, 1))),
        ("Stacked", 1, 6, ((0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5))),
    ),
    7: (
        ("Grid, wide last", 2, 4, ((0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2), (0, 3, 2, 1))),
        ("Wide over six", 2, 4, ((0, 0, 2, 1), (0, 1), (1, 1), (0, 2), (1, 2), (0, 3), (1, 3))),
        ("Three wide, wide last", 3, 3, ((0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (0, 2, 3, 1))),
        ("Big left", 3, 3, ((0, 0, 1, 3), (1, 0), (2, 0), (1, 1), (2, 1), (1, 2), (2, 2))),
    ),
    8: (
        ("Grid, two wide", 2, 4, ((0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2), (0, 3), (1, 3))),
        ("Grid, four wide", 4, 2, ((0, 0), (1, 0), (2, 0), (3, 0), (0, 1), (1, 1), (2, 1), (3, 1))),
    ),
}

def _cell(cell):
    if len(cell) == 4:
        return cell
    return (cell[0], cell[1], 1, 1)


# A layout whose cells do not tile its grid exactly, one per field, is
# not offered; the check keeps the table honest as layouts are added.
def _valid(layout, count):
    _name, cols, rows, cells = layout
    if len(cells) != count:
        return False
    seen = set()
    for cell in cells:
        c, r, cs, rs = _cell(cell)
        if cs < 1 or rs < 1 or c + cs > cols or r + rs > rows:
            return False
        for x in range(c, c + cs):
            for y in range(r, r + rs):
                if (x, y) in seen:
                    return False
                seen.add((x, y))
    return len(seen) == cols * rows

LAYOUTS = {n: tuple(l for l in options if _valid(l, n))
           for n, options in LAYOUTS.items()}


def _auto(count):
    """The arrangement used before layouts were a choice, for any count."""
    cols = 1 if count <= 2 else 2
    rows = (count + cols - 1) // cols
    cells = []
    for i in range(count):
        c, r = i % cols, i // cols
        if cols == 2 and i == count - 1 and count % 2 == 1:
            cells.append((0, r, 2, 1))
        else:
            cells.append((c, r, 1, 1))
    return ("Auto", cols, rows, tuple(cells))


def layout_names(count):
    return [l[0] for l in LAYOUTS.get(count, ())]


def layout_for(name, count):
    """The layout to draw `count` fields with: the one called `name` if
    it exists for that count, else the default for the count."""
    options = LAYOUTS.get(count)
    if not options:
        return _auto(count)
    for option in options:
        if option[0] == name:
            return option
    return options[0]


def cell_rects(layout, width, height, pad):
    """Pixel (x, y, w, h) for each slot of `layout` inside width x height,
    with `pad` between cells and around the edge, plus each slot's
    effective row count (rows // rowspan) for font sizing."""
    _name, cols, rows, cells = layout
    cw = (width - pad * (cols + 1)) // cols
    ch = (height - pad * (rows + 1)) // rows
    out = []
    for cell in cells:
        c, r, cs, rs = _cell(cell)
        x = pad + c * (cw + pad)
        y = pad + r * (ch + pad)
        w = cs * cw + (cs - 1) * pad
        if c + cs == cols:
            w = width - pad - x          # absorb the integer-division remainder
        h = rs * ch + (rs - 1) * pad
        out.append((x, y, w, h, max(1, rows // rs)))
    return out
