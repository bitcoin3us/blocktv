# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of BlockTV. BlockTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""Every change to BlockTV's data cache reaches flash, not only the first.

cache.json keeps the last readings, the price series, the archive of other
currencies and the update stamps, so a restart shows them at once. Since
MicroPythonOS 0.11, SharedPreferences skips a write when what it is given
equals the data it loaded or last saved. BlockTV handed it the state's own
dicts and lists and restored the state from the prefs' own, so both held
the same charts, archive and stamps. A change made to them in place
changed both sides, the editor found nothing new, and unless a reading
such as the height or the price changed as well, the write was skipped:
after a reboot the charts and stamps went back to an older save.

Each test reads the file back through a fresh SharedPreferences, as the
next boot would. A check of the data in memory passes either way.

Run from a MicroPythonOS checkout with the app linked in:

    ln -s /path/to/org.zaptv.blocktv internal_filesystem/apps/
    ./scripts/test_runner.py /path/to/tests/test_blocktv_cache.py
"""
import os
import sys
import time
import unittest

from mpos import SharedPreferences

FULLNAME = "org.zaptv.blocktv"
APP = "apps/" + FULLNAME
if APP not in sys.path:
    sys.path.insert(0, APP)
import blocktv

CACHE_FILE = "test_blocktv_cache.json"
HOUR = 3600
PRICE = 61700.0
# A full 7d range, one point an hour. _restore_cache clears fetched_at for a
# shorter one, and that change alone would make the next save write.
WEEK = [60000.0 + 10 * i for i in range(168)]


def _fresh_cache():
    """A new SharedPreferences on the test cache file: what the next boot
    reads from flash."""
    return SharedPreferences(FULLNAME, CACHE_FILE)


def _on_flash():
    return _fresh_cache().get_dict("data")


def _app():
    """A BlockTV with just what the cache code uses, on the test file."""
    app = blocktv.BlockTV()
    app.cache = _fresh_cache()
    app._cache_restored = False
    app._cache_saved_at = 0
    app.state = {"currency": "USD"}
    return app


def _state(now):
    """A dashboard that has been running a while, in USD, with EUR parked."""
    return {
        "currency": "USD", "series_currency": "USD",
        "fine_currency": "USD", "charts_currency": "USD",
        "height": 915000, "fee": 3, "fee_low": 1, "fee_high": 5,
        "balance": None, "zap": None,
        "price": PRICE,
        "fine": [61650.0, PRICE], "fine_ts": now - now % 300,
        "charts": {"7d": list(WEEK)},
        "charts_ts": {"7d": now - 2 * HOUR},
        "fetched_at": {"7d": now - 2 * HOUR},
        "ath": 73000.0, "ath_full": "USD",
        "archive": {"EUR": {
            "price": 52000.0, "fine": [52000.0], "fine_ts": now - 2 * HOUR,
            "charts": {"7d": [51500.0, 52000.0]},
            "charts_ts": {"7d": now - 2 * HOUR},
            "fetched_at": {"7d": now - 2 * HOUR},
            "ath": 65000.0, "ath_full": "EUR"}},
        "archive_order": ["EUR"],
        "updated_at": {"height": now, "price": now, "fees": now},
    }


class TestCacheReachesFlash(unittest.TestCase):

    def setUp(self):
        self._remove()
        self.now = int(time.time())

    def tearDown(self):
        self._remove()

    def _remove(self):
        try:
            os.remove(_fresh_cache().filepath)
        except OSError:
            pass

    def _saved_app(self):
        app = _app()
        app.state = _state(self.now)
        app._save_cache()
        return app

    def test_a_series_extended_in_place_after_a_save_reaches_flash(self):
        # The market loop's extend_series appends the spot price to a long
        # range and re-stamps it, both in place; the height, fees and price
        # stay as they were.
        app = self._saved_app()
        self.assertTrue(blocktv.extend_series(app.state))
        app._save_cache()
        disk = _on_flash()
        self.assertEqual(disk["charts"]["7d"][-2:], [WEEK[-1], PRICE])
        self.assertEqual(len(disk["charts"]["7d"]), len(WEEK))
        self.assertTrue(disk["charts_ts"]["7d"] > self.now - HOUR)

    def test_a_stamp_alone_reaches_flash(self):
        # A fetch that finds the same height re-stamps it, in place.
        app = self._saved_app()
        app.state["updated_at"]["height"] = self.now + 600
        app._save_cache()
        self.assertEqual(_on_flash()["updated_at"]["height"], self.now + 600)

    def test_a_sample_alone_reaches_flash(self):
        # Nothing but a list element changes, in place, as when record_fine
        # overwrites the last sample of a slot. The other save tests also
        # change a dict, which writes even while the lists are shared.
        app = self._saved_app()
        app.state["fine"][-1] = 61710.0
        app._save_cache()
        self.assertEqual(_on_flash()["fine"], [61650.0, 61710.0])

    def test_restored_series_changed_in_place_reach_flash(self):
        self._saved_app()
        app = _app()                     # the next start
        app._restore_cache()
        self.assertEqual(app.state["charts"]["7d"][-2:], WEEK[-2:])
        # Change nothing but restored series, in place, before any save.
        app.state["charts"]["7d"][-1] = 61690.0
        app.state["archive"]["EUR"]["charts"]["7d"].append(52300.0)
        app._save_cache()
        disk = _on_flash()
        self.assertEqual(disk["charts"]["7d"][-2:], [WEEK[-2], 61690.0])
        self.assertEqual(disk["archive"]["EUR"]["charts"]["7d"], [51500.0, 52000.0, 52300.0])

    def test_restored_changes_persist_where_get_dict_hands_out_the_stored_dict(self):
        # MPOS 0.11.0 and 0.11.1 returned prefs.data's own dict from get_dict.
        self._saved_app()
        app = _app()
        cache = app.cache
        cache.get_dict = lambda key, default=None: cache.data.get(key, default)
        app._restore_cache()
        app.state["charts"]["7d"][-1] = 61690.0
        app._save_cache()
        self.assertEqual(_on_flash()["charts"]["7d"][-2:], [WEEK[-2], 61690.0])

    def test_every_save_reaches_flash_not_only_the_next(self):
        # Two quiet market ticks, each saved: the second must land as well.
        app = self._saved_app()
        for _ in range(2):
            app.state["charts_ts"]["7d"] = self.now - 2 * HOUR
            self.assertTrue(blocktv.extend_series(app.state))
            app._save_cache()
        self.assertEqual(_on_flash()["charts"]["7d"][-3:], [WEEK[-1], PRICE, PRICE])

    def test_a_save_with_nothing_new_leaves_the_file_alone(self):
        # The copy must not turn every save into a flash write.
        app = self._saved_app()
        writes = []
        real = app.cache.save_config

        def counted():
            writes.append(1)
            real()
        app.cache.save_config = counted
        app._save_cache()
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
