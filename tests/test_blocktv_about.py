# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of BlockTV. BlockTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""The About screen follows the layout every ZapTV app shares.

Top to bottom: the logo lockup, LOGO_H (44 px) tall; a facts table with the
app version (read from MANIFEST.JSON at runtime), the MicroPythonOS version
and the hardware; the site; and a footer with the credit, the legal notice
and the third-party line. It must fit a 320x240 screen without scrolling,
even with the longest board name, without any text running under the back
button in the bottom-right corner, and in the OS light or dark theme.

This is a graphical test for the MicroPythonOS desktop build. From a
MicroPythonOS checkout, with the app linked in:

    ln -s /path/to/org.zaptv.blocktv internal_filesystem/apps/
    ./scripts/test_runner.py /path/to/tests/test_blocktv_about.py
"""
import io
import json
import sys
import unittest

import lvgl as lv
from mpos import (
    AppearanceManager,
    DisplayMetrics,
    FontManager,
    Intent,
    SharedPreferences,
)
from mpos.activity_navigator import ActivityNavigator
from mpos.build_info import BuildInfo
from mpos.device_info import DeviceInfo
from mpos.ui.testing import wait_for_render
from mpos.ui.view import screen_stack

FULLNAME = "org.zaptv.blocktv"
APP = "apps/" + FULLNAME
if APP not in sys.path:
    sys.path.insert(0, APP)
import blocktv

LONG_BOARD = "waveshare_esp32_s3_touch_lcd_3_5"
LONG_BOARD_PRETTY = "Waveshare ESP32 S3 Touch LCD 3 5"
SITE = "www.ZapTV.org"
CREDIT = "A fully open-source app\nby Richard Nakamoto"
LEGAL = "© 2026 ZapTV.org. Free software:\nGNU GPL v3 or later, no warranty."
THIRD_PARTY = "Shared modules from\nzaptv-lib (MIT)."
SUBTITLE = "Version, credits and licence"
LOGO_H = 44
THEME_PREFS = "com.micropythonos.settings"   # where MicroPythonOS keeps it


def _top():
    return screen_stack[-1][0]


def _manifest_version():
    with open(APP + "/MANIFEST.JSON") as handle:
        return json.load(handle)["version"]


def _coords(obj):
    area = lv.area_t()
    obj.get_coords(area)
    return area.x1, area.y1, area.x2, area.y2


def _overlap(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _content(screen):
    """The About screen top to bottom, as tuples: ("image",),
    ("label", text) or ("fact", name, value). The floating back button is
    returned on its own."""
    items, back = [], None
    for i in range(screen.get_child_count()):
        child = screen.get_child(i)
        if child.has_flag(lv.obj.FLAG.FLOATING):
            back = child
        elif isinstance(child, lv.image):
            items.append(("image",))
        elif isinstance(child, lv.label):
            items.append(("label", child.get_text()))
        else:
            items.append(("fact",) + tuple(
                child.get_child(j).get_text()
                for j in range(child.get_child_count())))
    return items, back


def _drawn(obj, out):
    """Every label and image under obj, back button excluded."""
    for i in range(obj.get_child_count()):
        child = obj.get_child(i)
        if child.has_flag(lv.obj.FLAG.FLOATING):
            continue
        if isinstance(child, (lv.label, lv.image)):
            out.append(child)
        _drawn(child, out)
    return out


def _rgb(color):
    return color.red, color.green, color.blue


def _set_theme(light):
    AppearanceManager.set_light_mode(light, SharedPreferences(THEME_PREFS))
    wait_for_render(5)


class TestBlockTVAbout(unittest.TestCase):

    def setUp(self):
        self.board = DeviceInfo.get_hardware_id()
        self.release = BuildInfo.version.release
        self.patched = {}

    def tearDown(self):
        self._close()
        DeviceInfo.set_hardware_id(self.board)
        BuildInfo.version.release = self.release
        module = blocktv.__dict__
        for name, original in self.patched.items():
            if original is None:
                module.pop(name, None)
            else:
                module[name] = original
        _set_theme(True)

    def _patch(self, name, value):
        """Replace a global the About code looks up (open shadows the
        builtin) until tearDown."""
        module = blocktv.__dict__
        if name not in self.patched:
            self.patched[name] = module.get(name)
        module[name] = value

    def _close(self):
        while type(_top()) in (blocktv.AboutActivity,
                               blocktv.MainSettingsActivity):
            _top().finish()
            wait_for_render(10)

    def _open_settings(self):
        intent = Intent(activity_class=blocktv.MainSettingsActivity)
        intent.app_fullname = FULLNAME
        intent.putExtra("prefs", SharedPreferences(FULLNAME))
        ActivityNavigator.startActivity(intent)
        wait_for_render(20)
        settings = _top()
        self.assertTrue(isinstance(settings, blocktv.MainSettingsActivity))
        return settings

    def _open_about(self):
        """Tap the About row, as a user would, and return the screen."""
        settings = self._open_settings()
        row = next(s for s in settings.settings if s.get("title") == "About")
        row["cont"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        self.assertTrue(isinstance(_top(), blocktv.AboutActivity))
        screen = lv.screen_active()
        screen.update_layout()
        return screen

    def test_settings_row_is_last_with_its_subtitle(self):
        settings = self._open_settings()
        last = settings.settings[-1]
        self.assertEqual(last["title"], "About")
        self.assertEqual(last["placeholder"], SUBTITLE)
        self.assertEqual(last["value_label"].get_text(), SUBTITLE)

    def test_content_and_strings_in_order(self):
        DeviceInfo.set_hardware_id(LONG_BOARD)
        items, back = _content(self._open_about())
        self.assertEqual(items, [
            ("image",),
            ("fact", "BlockTV", _manifest_version()),
            ("fact", "MicroPythonOS", BuildInfo.version.release),
            ("fact", "Hardware", LONG_BOARD_PRETTY),
            ("label", SITE),
            ("label", CREDIT),
            ("label", LEGAL),
            ("label", THIRD_PARTY),
        ])
        self.assertTrue(back is not None, "no floating back button")

    def test_styles(self):
        screen = self._open_about()
        logo = screen.get_child(0)
        x1, y1, x2, y2 = _coords(logo)
        self.assertEqual(y2 - y1 + 1, LOGO_H)
        self.assertTrue(abs((x1 + x2) - (DisplayMetrics.width() - 1)) <= 2,
                        f"logo not centred: {(x1, x2)}")
        fonts = {size: FontManager.getFont(size=size).line_height
                 for size in (12, 14, 16)}
        for i in (1, 2, 3):
            for j in (0, 1):
                label = screen.get_child(i).get_child(j)
                self.assertEqual(label.get_style_text_font(0).line_height,
                                 fonts[14])
            name = screen.get_child(i).get_child(0)
            self.assertEqual(name.get_style_text_opa(0), lv.OPA._60)
            value = screen.get_child(i).get_child(1)
            self.assertEqual(value.get_style_text_align(0),
                             lv.TEXT_ALIGN.RIGHT)
        self.assertEqual(
            screen.get_child(4).get_style_text_font(0).line_height, fonts[16])
        for i in (5, 6, 7):
            label = screen.get_child(i)
            self.assertEqual(label.get_style_text_font(0).line_height,
                             fonts[12])
            self.assertEqual(label.get_style_text_opa(0), lv.OPA._60)
            self.assertEqual(label.get_style_text_align(0),
                             lv.TEXT_ALIGN.CENTER)
            self.assertEqual(label.get_long_mode(), lv.label.LONG_MODE.WRAP)
            self.assertEqual(label.get_width(), DisplayMetrics.width() - 100)
            # Broken by hand: LVGL wraps none of the footer's lines.
            lines = label.get_text().count("\n") + 1
            pitch = fonts[12] + label.get_style_text_line_space(0)
            self.assertEqual(label.get_height(),
                             fonts[12] + (lines - 1) * pitch)
        self.assertEqual(screen.get_scrollbar_mode(), lv.SCROLLBAR_MODE.OFF)

    def test_version_is_read_from_the_manifest_at_runtime(self):
        tried = []

        def fake_open(path, *args):
            tried.append(path)
            if path.endswith("/MANIFEST.JSON"):
                return io.StringIO('{"version": "9.8.7-test"}')
            raise OSError(2)

        self._patch("open", fake_open)
        items, _ = _content(self._open_about())
        self.assertEqual(items[1], ("fact", "BlockTV", "9.8.7-test"))
        self.assertEqual(tried[0], f"/apps/{FULLNAME}/MANIFEST.JSON")

    def test_facts_fall_back_to_unknown(self):
        def no_file(path, *args):
            raise OSError(2)

        self._patch("open", no_file)
        DeviceInfo.set_hardware_id(None)
        BuildInfo.version.release = None
        items, _ = _content(self._open_about())
        self.assertEqual(items[1:4], [("fact", "BlockTV", "unknown"),
                                      ("fact", "MicroPythonOS", "unknown"),
                                      ("fact", "Hardware", "unknown")])

    def test_logo_falls_back_to_the_app_name(self):
        for path in (None, "M:" + APP + "/MANIFEST.JSON"):   # missing, not a PNG
            self._patch("resolve_drawable", lambda fullname, name, p=path: p)
            items, _ = _content(self._open_about())
            self.assertEqual(items[0], ("label", "BlockTV"))
            self.assertEqual(items[1][:2], ("fact", "BlockTV"))
            self._close()

    def test_fits_320x240_with_the_longest_board_name(self):
        self.assertEqual((DisplayMetrics.width(), DisplayMetrics.height()),
                         (320, 240))
        DeviceInfo.set_hardware_id(LONG_BOARD)
        screen = self._open_about()
        hardware = screen.get_child(3).get_child(1)
        self.assertTrue(hardware.get_height() > 20, "board name did not wrap")
        overflow = screen.get_scroll_bottom()
        self.assertTrue(overflow <= 0, f"About scrolls by {overflow} px")

    def test_back_button_is_bottom_right_and_clear_of_content(self):
        DeviceInfo.set_hardware_id(LONG_BOARD)
        screen = self._open_about()
        _, back = _content(screen)
        w, h = DisplayMetrics.width(), DisplayMetrics.height()
        box = _coords(back)
        self.assertEqual(box, (w - 50, h - 50, w - 1, h - 1))
        for obj in _drawn(screen, []):
            text = obj.get_text() if isinstance(obj, lv.label) else "logo"
            self.assertFalse(_overlap(_coords(obj), box),
                             f"{text!r} runs under the back button")
        back.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        self.assertTrue(isinstance(_top(), blocktv.MainSettingsActivity))

    def test_colours_come_from_the_os_theme(self):
        seen = {}
        for light in (True, False):
            _set_theme(light)
            screen = self._open_about()
            reference = lv.obj()                  # a plain themed screen
            ref_label = lv.label(reference)
            expected = (_rgb(reference.get_style_bg_color(0)),
                        _rgb(ref_label.get_style_text_color(0)))
            reference.delete()
            self.assertEqual(_rgb(screen.get_style_bg_color(0)), expected[0])
            self.assertEqual(screen.get_style_bg_opa(0), lv.OPA.COVER)
            for label in _drawn(screen, []):
                if isinstance(label, lv.label):
                    self.assertEqual(_rgb(label.get_style_text_color(0)),
                                     expected[1], label.get_text())
            seen[light] = expected
            self._close()
        self.assertTrue(seen[True] != seen[False], "theme did not switch")


if __name__ == "__main__":
    unittest.main()
