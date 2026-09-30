# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of BlockTV. BlockTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""BlockTV must load its own modules, whenever it imports them.

All MicroPythonOS apps share one sys.modules, so importing a module name that
another app already imported silently returns that app's module. Lightning
Piggy and the MicroPythonOS Nostr app both ship a nostr_service.py, and
ClankerTV ships clankertv_core.py and clankertv_providers.py from another
zaptv-lib revision; whichever app imported first in a boot served the others.

MicroPythonOS also puts an app's directory on sys.path only while it imports
the entrypoint and runs its first onCreate/onStart/onResume. BlockTV imports
its Nostr service later, when zaps or wallet connect are configured, which
raised "no module named 'nostr_service'" after configuring them in settings.

Run from a MicroPythonOS checkout with the app linked in:

    ln -s /path/to/org.zaptv.blocktv internal_filesystem/apps/
    ./scripts/test_runner.py /path/to/tests/test_blocktv_module_names.py
"""
import os
import sys
import unittest

APP = "apps/org.zaptv.blocktv"

# The names BlockTV's helpers had before they were prefixed; other apps use
# some of them, and the rest are generic enough to.
GENERIC = ("clankertv_core", "clankertv_providers", "field_picker", "fields",
           "layouts", "market_data", "nostr_service", "odometer",
           "zap_service")


class _Impostor:
    """Stands in for another app's module of the same name. Any attribute
    BlockTV imports from it is a class that says where it came from."""

    def __init__(self, name):
        self.__name__ = name

    def __getattr__(self, attr):
        cls = type(attr, (), {})
        cls.impostor = self.__name__
        return cls


def _app_modules():
    return [f[:-3] for f in os.listdir(APP) if f.endswith(".py")]


def _leave_sys_path():
    """What AppManager.execute_script does once the first lifecycle
    callbacks have returned."""
    while APP in sys.path:
        sys.path.remove(APP)


class _FreshImport:
    """Save every module name involved, let the test rearrange sys.modules,
    import blocktv fresh, and put everything back afterwards."""

    def __init__(self):
        names = set(GENERIC) | set(_app_modules())
        self.saved = {n: sys.modules[n] for n in names if n in sys.modules}
        self.names = names
        self.path = sys.path[:]
        for n in names:
            sys.modules.pop(n, None)
        if APP not in sys.path:
            sys.path.insert(0, APP)

    def restore(self):
        nostr = sys.modules.get("blocktv_nostr_service")
        if nostr is not None and nostr.NostrManager._instance is not None:
            mgr = nostr.NostrManager._instance
            mgr.stop()
            if mgr._cm_callback is not None:
                from mpos import ConnectivityManager
                ConnectivityManager.unregister_callback(mgr._cm_callback)
        for n in self.names:
            sys.modules.pop(n, None)
        sys.modules.update(self.saved)
        sys.path[:] = self.path


class TestModuleNames(unittest.TestCase):

    def test_every_helper_module_has_the_blocktv_prefix(self):
        helpers = [m for m in _app_modules() if m != "blocktv"]
        self.assertTrue(helpers, "no helper modules found in " + APP)
        clashes = [m for m in helpers if not m.startswith("blocktv_")]
        self.assertEqual(clashes, [], "helper modules without the blocktv_ prefix")

    def test_blocktv_uses_its_own_modules_when_another_app_loaded_first(self):
        fresh = _FreshImport()
        try:
            for name in GENERIC:
                sys.modules[name] = _Impostor(name)
            import blocktv
            borrowed = [k for k, v in blocktv.__dict__.items()
                        if getattr(v, "impostor", None)]
            self.assertEqual(borrowed, [], "names taken from another app's modules")
            for attr, module, name in (
                    ("render_field", "blocktv_fields", "render_field"),
                    ("fmt_int", "blocktv_fields", "fmt_int"),
                    ("MarketData", "blocktv_market_data", "MarketData"),
                    ("layout_for", "blocktv_layouts", "layout_for"),
                    ("Odometer", "blocktv_odometer", "Odometer"),
                    ("ZapMonitor", "blocktv_zap_service", "ZapMonitor"),
                    ("FieldPickerActivity", "blocktv_field_picker", "FieldPickerActivity"),
                    ("ai_level", "blocktv_clankertv_core", "level"),
                    ("ai_fetch_all", "blocktv_clankertv_providers", "fetch_all")):
                self.assertTrue(getattr(blocktv, attr) is getattr(sys.modules[module], name),
                                attr + " is not " + module + "." + name)
            # ...and the helpers' imports of each other are BlockTV's too.
            self.assertTrue(sys.modules["blocktv_clankertv_providers"].core
                            is sys.modules["blocktv_clankertv_core"])
            self.assertTrue(sys.modules["blocktv_fields"].fine_series
                            is sys.modules["blocktv_market_data"].fine_series)
        finally:
            fresh.restore()

    def test_blocktv_leaves_generic_names_free_for_other_apps(self):
        fresh = _FreshImport()
        try:
            import blocktv  # noqa: F401
            taken = [n for n in GENERIC if n in sys.modules]
            self.assertEqual(taken, [], "BlockTV claimed generic module names")
        finally:
            fresh.restore()


class TestDeferredNostrImport(unittest.TestCase):
    """Zaps or wallet connect configured after launch: onResume starts the
    ZapMonitor with BlockTV's directory already gone from sys.path."""

    def _start_monitor(self, blocktv):
        monitor = blocktv.ZapMonitor()
        monitor.start()
        try:
            nostr = sys.modules["blocktv_nostr_service"]
            mgr = nostr.NostrManager.get_instance()
            self.assertTrue(mgr.is_running())
            self.assertTrue(sys.modules["blocktv_zap_service"]._manager()
                            is nostr.NostrManager)
        finally:
            monitor.stop()

    def test_zap_monitor_starts_after_the_app_dir_leaves_sys_path(self):
        fresh = _FreshImport()
        try:
            import blocktv
            _leave_sys_path()
            self._start_monitor(blocktv)
            self.assertFalse(APP in sys.path, "the deferred import left the app dir on sys.path")
        finally:
            fresh.restore()

    def test_zap_monitor_ignores_another_apps_nostr_service(self):
        fresh = _FreshImport()
        try:
            import blocktv
            _leave_sys_path()
            sys.modules["nostr_service"] = _Impostor("nostr_service")
            self._start_monitor(blocktv)
        finally:
            fresh.restore()


if __name__ == "__main__":
    unittest.main()
