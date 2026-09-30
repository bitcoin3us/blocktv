# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of BlockTV. BlockTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""The AI usage core BlockTV vendors from zaptv-lib, run on MicroPython.

ClankerTV's unit tests run the same module on CPython, which hid a
str.title() call that MicroPython does not have.

    ./scripts/test_runner.py /path/to/tests/test_blocktv_usage_core.py
"""
import sys
import unittest

APP = "apps/org.zaptv.blocktv"

sys.path.insert(0, APP)
import blocktv_clankertv_core as core  # noqa: E402
sys.path.remove(APP)

NOW = 1790157600  # 2026-09-23 10:00:00 UTC


class TestUsageApi(unittest.TestCase):

    def test_weekly_bucket_for_a_model_not_listed_yet(self):
        doc = {"five_hour": {"utilization": 1, "resets_at": "2026-09-23T12:00:00Z"},
               "seven_day_fable": {"utilization": 55, "resets_at": "2026-09-26T10:00:00Z"},
               "seven_day_new_thing": {"utilization": 5, "resets_at": "2026-09-26T10:00:00Z"}}
        rec = core.claude_from_usage_api(doc, NOW)
        self.assertEqual([(m["label"], m["pct"]) for m in rec["meters"]],
                         [("Session", 1.0), ("Weekly Fable", 55.0), ("Weekly New Thing", 5.0)])


if __name__ == "__main__":
    unittest.main()
