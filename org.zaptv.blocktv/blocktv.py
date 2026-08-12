# BlockTV — a clean, customisable Bitcoin dashboard for MicroPythonOS.
#
# Data fields: block height, spot price, moscow time, halving countdown,
# median fee rate, circulating supply, market cap, clock, latest nostr
# zap, NWC wallet balance.
#
# The user composes their own screens (one or more fields per screen)
# and swipes left/right between them. Layout adapts to the field count.

import json
import os
import time

import lvgl as lv

from mpos import (
    Activity,
    add_focus_border,
    DisplayMetrics,
    FontManager,
    Intent,
    SettingsActivity,
    SharedPreferences,
    TaskManager,
)
import mpos.time

from fields import (
    CHART_FIELDS, CHART_LABELS, FIELD_CATEGORIES, FIELD_IDS, FIELD_SOURCES,
    FIELD_TITLES, MONTHS, WEEKDAYS, chart_series, render_field,
)
from market_data import (
    MarketData, CURRENCIES, DEFAULT_BASE_URL, FINE_SLOT_SECONDS,
    RANGE_REFRESH, RANGE_SPECS, extend_series, record_fine,
    resample_fine, switch_currency,
)
from odometer import Odometer
from zap_service import ZapMonitor

# Defaults chosen by probing each relay for kind-9735 zap receipts, not
# just for a successful handshake: relay.damus.io accepted the connection
# but served no zap receipts at all, which is why it is not here.
DEFAULT_RELAYS = ("wss://relay.primal.net,wss://nos.lol,"
                  "wss://relay.snort.social,wss://offchain.pub")
DEFAULT_REFRESH_SECONDS = 60
# What a fresh install opens on. The 24h chart leads because it is the
# one field that fills the screen and shows the app doing something the
# moment the first fetch lands; the grids behind it are the reference
# numbers. Only used when no screens have been saved yet — an existing
# install keeps whatever the user composed.
DEFAULT_SCREENS = [
    ["price_chart"],
    ["block_height", "spot_price", "moscow_time", "fee_rate"],
    ["halving", "supply", "market_cap", "clock"],
]
MAX_FIELDS_PER_SCREEN = 8
SPLASH_SECONDS = 5
LOGO_SPLASH_MS = 2000       # start-screen logo duration (tap to skip)
LOGO_TAP_GRACE = 0.7        # ignore taps this long after the logo appears
# Two renderings of the same logo: the artwork as drawn for light
# backgrounds, and an ink-inverted copy (black parts turned white, gold
# left alone) for dark ones.
LOGO_ASSET_LIGHT = "blocktv_logo_light.png"
LOGO_ASSET_DARK = "blocktv_logo_dark.png"
APP_SITE = "www.ZapTV.org"
_HW_ACRONYMS = ("lcd", "oled", "tft", "gps", "imu", "ir", "sd", "usb", "tv")
# A move worth a second look, per range — scaled to what is ordinary for
# each window, so a 3% day shouts and a 3% year does not. Past these the
# trend pill's text steps up a size.
PCT_EMPHASIS = {"24h": 2.0, "7d": 5.0, "30d": 10.0, "1y": 40.0,
                "4y": 100.0}
PRIORITY_FEE_FIELDS = ("fee_low", "fee_high")
PILL_BUMP = 4               # pill font size, over the tile's small font
PILL_BUMP_BIG = 8           # ...and when the move clears its threshold
FLASH_MS = 150              # attention flash duration
SETTINGS_HIT_PAD = 12       # grows the cog's touch target beyond its 30 px art
CLOCK_PAD = 2               # corner clock's gap from the bottom edge
CLOCK_LINE = 16             # its line height at font 12 — the cog aligns to it
SWIPE_THRESHOLD = 40        # px of travel before a swipe turns the page
SWIPE_RATIO = 1.2           # how much more horizontal than vertical it must be
BUTTON_GPIO = 0             # BOOT button: an extra way to page through screens
BUTTON_POLL_S = 0.05
CACHE_SAVE_SECONDS = 600    # throttle for persisting the data cache

# Stale-data warning: a field's title turns orange once its source is
# 10 minutes late, red once it is 24 hours late. "Late" is measured from
# when the next update was due (last success + expected cadence).
STALE_ORANGE_SECONDS = 10 * 60
STALE_RED_SECONDS = 24 * 3600
NWC_CADENCE_SECONDS = 120   # NostrManager polls the wallet every 120s
NOSTR_CADENCE_SECONDS = 60  # relay health is stamped every 30s when live

BITCOIN_ORANGE = 0xF7931A
NOSTR_PURPLE = 0xA24DFF

# Text and background are picked independently in Settings.
COLOR_OPTIONS = [
    ("White", "ffffff"),
    ("Black", "000000"),
    ("Bitcoin Orange", "f7931a"),
    ("Nostr Purple", "a24dff"),
]
DEFAULT_FG = "ffffff"
DEFAULT_BG = "000000"

# Migration map for the retired fixed-theme preference.
_LEGACY_THEMES = {
    "dark": ("ffffff", "000000"),
    "light": ("000000", "ffffff"),
    "orange": ("f7931a", "000000"),
    "orange_inv": ("000000", "f7931a"),
    "purple": ("a24dff", "000000"),
    "purple_inv": ("000000", "a24dff"),
}


def _lum(color):
    """Approximate perceived luminance, 0..255."""
    r = (color >> 16) & 255
    g = (color >> 8) & 255
    b = color & 255
    return (2126 * r + 7152 * g + 722 * b) // 10000


def _dist(a, b):
    """Manhattan RGB distance, 0..765."""
    return (abs(((a >> 16) & 255) - ((b >> 16) & 255))
            + abs(((a >> 8) & 255) - ((b >> 8) & 255))
            + abs((a & 255) - (b & 255)))


def _pick_visible(candidates, fg, bg):
    """First candidate that stands apart from both text and background."""
    for color in candidates:
        if _dist(color, fg) > 150 and _dist(color, bg) > 150:
            return color
    return candidates[0]


def _knockout(fill):
    """Text colour to punch out of a filled pill. Chosen from the fill's
    own luminance rather than the theme, so the badge stays legible
    whatever colours the user picked for text and background."""
    return 0x000000 if _lum(fill) > 140 else 0xFFFFFF


def _stale_palette(fg, bg):
    """(10min, 24h) warning colors that stay visible against any fg/bg
    combination — e.g. amber is useless on the orange themes."""
    if _lum(bg) > 140:
        c10 = (0xEF6C00, 0xFFFFFF, 0x000000)
        c24 = (0xB71C1C, 0x8E0000, 0xD32F2F)
    else:
        c10 = (0xFFA000, 0xFFEB3B, 0xFFFFFF)
        c24 = (0xE53935, 0xB71C1C, 0xFF6E6E)
    return _pick_visible(c10, fg, bg), _pick_visible(c24, fg, bg)


def _trend_palette(bg):
    """(up, down) line colors — brighter on dark backgrounds, deeper on
    light ones, so both stay legible."""
    if _lum(bg) > 140:
        return 0x00A63C, 0xD32F2F
    return 0x00E676, 0xFF5252


def _decimate(points, max_points):
    """Thin a series down to at most max_points, always keeping the newest
    sample so the line ends where the price actually is."""
    if len(points) <= max_points or max_points < 2:
        return points
    step = len(points) / max_points
    out = [points[int(i * step)] for i in range(max_points)]
    out[-1] = points[-1]
    return out


def _parse_color(value, default):
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return default

# Builtin Montserrat sizes, largest first — fallback when the bundled TTF
# is unavailable (no tiny_ttf in the build, file missing, ...).
_FONT_LADDER = (28, 24, 20, 18, 16, 14, 12, 10, 8)

# Bundled bold TTF for arbitrarily large value text (Roboto Bold subset,
# ASCII only — symbols/emoji still come from the builtin fonts).
# Flat layout (MPOS 0.13+) first, then the pre-0.13 assets/ mirror so a
# package installed on an older device still finds its font.
_TTF_CANDIDATES = (
    "/apps/{}/bt_bold.ttf",          # device
    "apps/{}/bt_bold.ttf",           # desktop (CWD = internal_filesystem)
    "/apps/{}/assets/bt_bold.ttf",   # pre-0.13 nested layout
    "apps/{}/assets/bt_bold.ttf",
)

# Average glyph advance relative to font size for Roboto Bold digits.
_CHAR_WIDTH_FACTOR = 0.60


def resolve_ttf(fullname):
    """LVGL-filesystem path ("M:" prefix, required by tiny_ttf) of the
    bundled TTF, or None if unavailable."""
    if not hasattr(lv, "tiny_ttf_create_file"):
        return None
    for pattern in _TTF_CANDIDATES:
        path = pattern.format(fullname)
        try:
            os.stat(path)
            return "M:" + path
        except OSError:
            pass
    return None


def resolve_drawable(fullname, name):
    """LVGL-filesystem path of a bundled res/drawable-mdpi image."""
    for prefix in ("/apps/{}/res/drawable-mdpi/{}", "apps/{}/res/drawable-mdpi/{}"):
        path = prefix.format(fullname, name)
        try:
            os.stat(path)
            return "M:" + path
        except OSError:
            pass
    return None


def _hardware_id():
    try:
        from mpos.device_info import DeviceInfo
        return DeviceInfo.get_hardware_id()
    except Exception:
        return "unknown"


def _os_version():
    try:
        from mpos.build_info import BuildInfo
        return BuildInfo.version.release
    except Exception:
        return "unknown"


def _pretty_hardware(board):
    """waveshare_esp32_s3_touch_lcd_2 -> Waveshare ESP32 S3 Touch LCD 2."""
    words = []
    for token in str(board).replace("-", "_").split("_"):
        if not token:
            continue
        # Model codes (esp32, s3, m5stack) and hardware acronyms both read
        # wrong in title case, so anything with a digit stays uppercase.
        if token in _HW_ACRONYMS or any(ch in "0123456789" for ch in token):
            words.append(token.upper())
        else:
            words.append(token[0].upper() + token[1:])
    return " ".join(words) or str(board)


def app_version(fullname):
    """Our own version, read from the manifest we shipped with."""
    for path in ("/apps/{}/MANIFEST.JSON", "apps/{}/MANIFEST.JSON"):
        try:
            with open(path.format(fullname)) as handle:
                return json.load(handle).get("version") or "unknown"
        except Exception:
            pass
    return "unknown"


def fit_size(text, max_w, max_h):
    """Largest font size whose text fits max_w wide and max_h tall."""
    length = max(1, len(text))
    return max(12, min(int(max_h), int(max_w / (_CHAR_WIDTH_FACTOR * length))))


def _button_row(parent):
    """Transparent horizontal container so two buttons share one line
    instead of each eating a row of scroll space."""
    row = lv.obj(parent)
    row.set_width(lv.pct(100))
    row.set_height(lv.SIZE_CONTENT)
    row.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
    row.set_style_border_width(0, lv.PART.MAIN)
    row.set_style_pad_all(0, lv.PART.MAIN)
    row.set_style_pad_column(6, lv.PART.MAIN)
    row.set_flex_flow(lv.FLEX_FLOW.ROW)
    row.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
    row.remove_flag(lv.obj.FLAG.SCROLLABLE)
    return row


def _row_button(row, text, on_click, grow=1):
    btn = lv.button(row)
    btn.set_flex_grow(grow)
    btn.add_event_cb(lambda e: on_click(), lv.EVENT.CLICKED, None)
    # Joins the focus group: on a device with no touchscreen this is the
    # only way the button can be reached at all.
    add_focus_border(btn)
    label = lv.label(btn)
    label.set_text(text)
    label.center()
    return btn


def _no_scroll_chain(obj):
    """Stop a drag on this widget from scrolling the page behind it.

    LVGL hands an unhandled scroll gesture up to the nearest scrollable
    ancestor; the field editor scrolls, so without this a slide meant to
    reorder would take the whole list with it."""
    for flag in ("SCROLL_CHAIN_VER", "SCROLL_CHAIN"):
        value = getattr(lv.obj.FLAG, flag, None)
        if value is not None:
            obj.remove_flag(value)
            return


def _add_floating_back(screen, on_click):
    """Floating return button pinned bottom-right, matching the affordance
    the Lightning Piggy app uses on its settings screens. FLOATING keeps
    it in place while the settings list scrolls behind it."""
    btn = lv.obj(screen)
    btn.set_size(50, 50)
    btn.align(lv.ALIGN.BOTTOM_RIGHT, 0, 0)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)
    btn.add_flag(lv.obj.FLAG.FLOATING)
    btn.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
    btn.set_style_border_width(0, lv.PART.MAIN)
    btn.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
    btn.remove_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_event_cb(lambda e: on_click(), lv.EVENT.CLICKED, None)
    add_focus_border(btn)
    icon = lv.label(btn)
    icon.set_text(lv.SYMBOL.NEW_LINE)
    icon.set_style_text_font(FontManager.getFont(size=24), lv.PART.MAIN)
    icon.center()
    return btn


def load_screens(prefs):
    raw = prefs.get_string("screens_json")
    if raw:
        try:
            screens = json.loads(raw)
            cleaned = []
            for fields in screens:
                valid = [f for f in fields if f in FIELD_IDS]
                if valid:
                    cleaned.append(valid[:MAX_FIELDS_PER_SCREEN])
            if cleaned:
                return cleaned
        except Exception as e:
            print("BlockTV: bad screens_json, using defaults: {}".format(e))
    return [list(s) for s in DEFAULT_SCREENS]


def save_screens(prefs, screens):
    editor = prefs.edit()
    editor.put_string("screens_json", json.dumps(screens))
    editor.commit()


def theme_colors(prefs):
    """(bg, fg, stale_orange, stale_red) from the independent text/
    background color prefs. bg/fg are lv colors; stale pair stays hex.
    Same-on-same picks a contrasting text color instead of vanishing."""
    fg = _parse_color(prefs.get_string("fg_color", DEFAULT_FG), 0xFFFFFF)
    bg = _parse_color(prefs.get_string("bg_color", DEFAULT_BG), 0x000000)
    if fg == bg:
        fg = 0x000000 if _lum(bg) > 140 else 0xFFFFFF
    stale10, stale24 = _stale_palette(fg, bg)
    return lv.color_hex(bg), lv.color_hex(fg), stale10, stale24


def migrate_theme_pref(prefs):
    """One-time move from the fixed 'theme' pref to fg_color/bg_color."""
    legacy = prefs.get_string("theme")
    if not legacy:
        return
    editor = prefs.edit()
    if legacy in _LEGACY_THEMES and not prefs.get_string("fg_color"):
        fg, bg = _LEGACY_THEMES[legacy]
        editor.put_string("fg_color", fg)
        editor.put_string("bg_color", bg)
    editor.put_string("theme", None)
    editor.commit()


class BlockTV(Activity):

    def onCreate(self):
        self.prefs = SharedPreferences(self.appFullName)
        self.cache = SharedPreferences(self.appFullName, "cache.json")
        migrate_theme_pref(self.prefs)
        self._cache_saved_at = 0
        self._cache_restored = False
        self.keep_running = False
        self.current_page = 0
        self.state = {
            "height": None, "price": None, "fee": None,
            "fee_low": None, "fee_high": None,
            "currency": "USD", "zap": None, "balance": None,
            "localtime": None,
        }
        self.market = None
        self.zap_monitor = ZapMonitor()
        self._clock_timer = None
        self._market_task = None
        self._tile_labels = {}
        self._page_conts = {}
        self._page_tiles = {}
        self._chart_loading = {}
        self._history_active = False
        self._history_started = 0
        self._history_bytes = 0
        self._history_rate = 0
        self._page_version = {}
        self._data_version = 0
        self._dots = []
        self._dots_space = 0
        self._corner_clock = None
        self._screen = None
        self._press_x = 0
        self._press_y = 0
        self._swipe_done = False
        self._button_pin = None
        self._button_task = None
        self._splash = None
        self._splash_until = 0
        self._loop_gen = 0
        self._stale_check_at = 0
        self._ttf = resolve_ttf(self.appFullName)
        self._logo_overlay = None
        self._logo_image = None
        self._logo_shown = False   # start screen appears once per launch
        try:
            # Defensive PNG decoder init (see MPOS_APP_DEV.md §6).
            lv.lodepng_init()
        except Exception:
            pass

        screen = lv.obj()
        screen.set_style_border_width(0, lv.PART.MAIN)
        screen.set_style_radius(0, lv.PART.MAIN)
        screen.set_style_pad_all(0, lv.PART.MAIN)
        screen.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        screen.remove_flag(lv.obj.FLAG.SCROLLABLE)
        screen.add_event_cb(self._on_pressed, lv.EVENT.PRESSED, None)
        screen.add_event_cb(self._on_pressing, lv.EVENT.PRESSING, None)
        screen.add_event_cb(self._on_released, lv.EVENT.RELEASED, None)
        self.setContentView(screen)

    # --- Lifecycle ---

    def onResume(self, screen):
        super().onResume(screen)
        self._load_config()
        if self.current_page >= len(self.screens):
            self.current_page = len(self.screens) - 1
        self._build_ui(screen)
        self._refresh_clock()
        if not self._logo_shown:
            self._logo_shown = True
            self._show_logo_splash(screen)

        self.keep_running = True
        # Bump the generation so any not-yet-exited loop from a previous
        # resume stops at its next tick instead of doubling up.
        self._loop_gen += 1
        self._market_task = TaskManager.create_task(self._market_loop(self._loop_gen))
        self._start_button_watcher()
        self._clock_timer = lv.timer_create(self._clock_tick, 1000, None)
        self._clock_timer.set_repeat_count(-1)

        if self.zap_npub or self.nwc_url:
            self.zap_monitor.start(
                npub=self.zap_npub,
                relays=self.zap_relays,
                nwc_url=self.nwc_url,
                on_zap=self._on_zap,
                on_balance=self._on_balance,
            )

        # Baseline for staleness: sources with no success yet count from
        # now, so a device that never reaches the network still escalates
        # to orange/red instead of staying quietly blank.
        stamps = self.state.setdefault("updated_at", {})
        now = time.time()
        active = ["height", "price", "fees"]
        if self.zap_npub:
            active.append("nostr")
        if self.nwc_url:
            active.append("nwc")
        for source in active:
            if source not in stamps:
                stamps[source] = now
        self._stale_check_at = now

    def onPause(self, screen):
        super().onPause(screen)
        self.keep_running = False
        if self._clock_timer:
            self._clock_timer.delete()
            self._clock_timer = None
        self.zap_monitor.stop()
        if self.state.get("height") is not None or self.state.get("price") is not None:
            self._save_cache()

    # --- Configuration ---

    def _load_config(self):
        self.screens = load_screens(self.prefs)
        self.bg, self.fg, self._stale_orange, self._stale_red = theme_colors(self.prefs)
        # Raw background hex drives the light/dark logo choice.
        self._bg_hex = _parse_color(self.prefs.get_string("bg_color", DEFAULT_BG), 0x000000)
        self._trend_up, self._trend_down = _trend_palette(self._bg_hex)
        currency = self.prefs.get_string("currency", "USD")
        self.state["currency"] = currency
        # Parks the outgoing currency's series and brings back the
        # incoming one's, if we still have it.
        switch_currency(self.state, currency)
        try:
            self.refresh_seconds = max(15, int(self.prefs.get_string(
                "refresh_seconds", str(DEFAULT_REFRESH_SECONDS))))
        except (TypeError, ValueError):
            self.refresh_seconds = DEFAULT_REFRESH_SECONDS
        self.zap_npub = self.prefs.get_string("zap_npub")
        self.zap_relays = self.prefs.get_string("zap_relays", DEFAULT_RELAYS)
        self.nwc_url = self.prefs.get_string("nwc_url")
        self.zap_splash_enabled = self.prefs.get_string("zap_splash", "on") == "on"
        self.flash_mode = self.prefs.get_string("flash_mode", "off")
        self.show_titles = self.prefs.get_string("show_titles", "on") == "on"
        self.show_corner_clock = self.prefs.get_string("corner_clock", "off") == "on"
        self.market = MarketData(self.prefs.get_string("mempool_url", DEFAULT_BASE_URL))
        # Last-known values from the previous session show instantly
        # (with honest stale coloring) instead of "--" placeholders.
        self._restore_cache()
        # Live values held by the monitor beat anything cached.
        if self.zap_monitor.last_zap:
            self.state["zap"] = self.zap_monitor.last_zap
        if self.zap_monitor.balance is not None:
            self.state["balance"] = self.zap_monitor.balance

    def _restore_cache(self):
        """Cold-start restore. Runs once: later resumes keep whatever is
        already in memory, and currency changes are handled by
        switch_currency rather than by re-reading the file."""
        if self._cache_restored:
            return
        self._cache_restored = True
        cached = self.cache.get_dict("data")
        if not cached:
            return
        state = self.state

        stamps = state.setdefault("updated_at", {})
        for source, when in (cached.get("updated_at") or {}).items():
            if source not in stamps:
                stamps[source] = when

        # Readings that mean the same thing in any currency.
        for key in ("height", "fee", "fee_low", "fee_high", "balance", "zap"):
            if state.get(key) is None and cached.get(key) is not None:
                state[key] = cached[key]

        # Currency-specific series: load them exactly as they were saved,
        # archive included, then switch to the configured currency — which
        # parks the saved set and brings back the right one.
        state["archive"] = cached.get("archive") or {}
        state["archive_order"] = list(cached.get("archive_order") or [])
        saved = cached.get("series_currency")
        if saved:
            state["series_currency"] = saved
            state["fine_currency"] = saved
            state["charts_currency"] = saved
            state["price"] = cached.get("price")
            fine = list(cached.get("fine") or [])
            cached_slot = cached.get("fine_slot") or 600
            if cached_slot != FINE_SLOT_SECONDS:
                # Recording predates a slot-size change: re-express it
                # rather than discard a day of real samples.
                fine = resample_fine(fine, cached_slot, FINE_SLOT_SECONDS)
            state["fine"] = fine
            state["fine_ts"] = cached.get("fine_ts")
            state["charts"] = cached.get("charts") or {}
            state["charts_ts"] = dict(cached.get("charts_ts") or {})
            # Cached series are as fresh as their last sample, so don't
            # re-download just because we rebooted.
            state["fetched_at"] = dict(cached.get("fetched_at")
                                       or cached.get("charts_ts") or {})
        switch_currency(state, state.get("currency", "USD"))

    def _save_cache(self):
        self._cache_saved_at = time.time()
        editor = self.cache.edit()
        editor.put_dict("data", {
            "height": self.state.get("height"),
            "fee": self.state.get("fee"),
            "fee_low": self.state.get("fee_low"),
            "fee_high": self.state.get("fee_high"),
            "balance": self.state.get("balance"),
            "zap": self.state.get("zap"),
            "series_currency": self.state.get("series_currency"),
            "price": self.state.get("price"),
            "charts": self.state.get("charts") or {},
            "charts_ts": self.state.get("charts_ts") or {},
            "fetched_at": self.state.get("fetched_at") or {},
            "fine": self.state.get("fine") or [],
            "fine_ts": self.state.get("fine_ts"),
            "fine_slot": FINE_SLOT_SECONDS,
            "archive": self.state.get("archive") or {},
            "archive_order": self.state.get("archive_order") or [],
            "updated_at": self.state.get("updated_at") or {},
        })
        editor.commit()

    # --- Staleness ---

    def _source_cadence(self, source):
        if source == "nwc":
            return NWC_CADENCE_SECONDS
        if source == "nostr":
            return NOSTR_CADENCE_SECONDS
        return self.refresh_seconds

    def _chart_freshness(self, field_id):
        """(stamp, cadence) for a chart field.

        Chart ranges are fetched independently and on very different
        cadences, so neither the shared "history" stamp nor the 60-second
        poll interval describes them. Using both was wrong in both
        directions: a 24h line refreshes every 30 minutes and so spent 20
        of every 30 wrongly marked late, while a 1y line refreshes weekly
        and looked fresh a day after its data stopped arriving."""
        label = CHART_LABELS.get(field_id)
        if label is None:                      # not a chart: shared stamp
            return ((self.state.get("updated_at") or {}).get("history"),
                    self.refresh_seconds)
        return ((self.state.get("charts_ts") or {}).get(label),
                RANGE_REFRESH.get(label, self.refresh_seconds))

    def _stale_color(self, field_id):
        """Warning color for a field's title, or None when fresh.
        Unconfigured nostr/NWC sources are not warnings."""
        stamps = self.state.get("updated_at") or {}
        worst = None
        for source in FIELD_SOURCES.get(field_id, ()):
            if source == "nostr" and not self.zap_npub:
                continue
            if source == "nwc" and not self.nwc_url:
                continue
            if source == "history":
                stamp, cadence = self._chart_freshness(field_id)
            else:
                stamp, cadence = stamps.get(source), self._source_cadence(source)
            if stamp is None:
                continue
            late = time.time() - stamp - cadence
            if worst is None or late > worst:
                worst = late
        if worst is None:
            return None
        if worst >= STALE_RED_SECONDS:
            return self._stale_red
        if worst >= STALE_ORANGE_SECONDS:
            return self._stale_orange
        return None

    def _apply_title_color(self, field_id):
        # When titles are hidden the warning lands on the unit/sub label
        # instead, so staleness stays visible.
        title, _value, sub = self._tile_labels[field_id][:3]
        if title is None and field_id in CHART_FIELDS:
            # A chart's sub label is the trend pill, and it already shows
            # staleness as its fill — recolouring its text here would only
            # fight that, and knock the text out of contrast with the fill.
            return
        target = title if title is not None else sub
        color = self._stale_color(field_id)
        if color is not None:
            target.set_style_text_color(lv.color_hex(color), lv.PART.MAIN)
            target.set_style_text_opa(lv.OPA.COVER, lv.PART.MAIN)
        else:
            target.set_style_text_color(self.fg, lv.PART.MAIN)
            target.set_style_text_opa(lv.OPA._50, lv.PART.MAIN)

    def _check_staleness(self):
        """Runs every ~30s from the clock timer: refresh relay-health
        stamps and re-color titles so warnings escalate over time even
        when no data is arriving."""
        stamps = self.state.setdefault("updated_at", {})
        if self.zap_npub and self.zap_monitor.is_connected():
            stamps["nostr"] = time.time()
        for field_id in self._tile_labels:
            self._apply_title_color(field_id)

    # --- Fonts ---

    def _value_font(self, size):
        """Font for value text at (approximately) the requested pixel size.
        Uses the bundled TTF when available so a lone field can fill the
        whole screen; falls back to the builtin Montserrat ladder."""
        size = max(12, min(int(size), 160))
        if self._ttf:
            # Quantize to multiples of 4 to bound the tiny_ttf cache.
            quantized = max(12, (size // 4) * 4)
            try:
                return FontManager.getFont(size=quantized, ttf=self._ttf)
            except Exception as e:
                print("BlockTV: TTF font failed, using builtins: {}".format(e))
                self._ttf = None
        for s in _FONT_LADDER:
            if s <= size:
                return FontManager.getFont(size=s)
        return FontManager.getFont(size=_FONT_LADDER[-1])

    # --- UI construction ---

    def _build_ui(self, screen):
        """Full rebuild — used on resume and whenever settings change.

        Individual pages are then cached: turning a page only toggles
        visibility, because rebuilding one costs ~0.4-1.3 s on the ESP32
        and that delay is exactly what made swiping feel unresponsive."""
        screen.clean()
        self._tile_labels = {}
        self._page_conts = {}
        self._page_tiles = {}
        self._page_version = {}
        self._chart_loading = {}
        self._dots = []
        self._splash = None
        self._logo_overlay = None   # destroyed by screen.clean() above
        self._logo_image = None
        screen.set_style_bg_color(self.bg, lv.PART.MAIN)
        screen.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        self._screen = screen

        width = DisplayMetrics.width()
        height = DisplayMetrics.height()
        self._dots_space = (max(10, height // 20) if len(self.screens) > 1
                            else max(4, width // 60))
        self._make_dots(screen, width, height, self._dots_space)
        self._make_settings_button(screen)
        screen.add_event_cb(self._on_key, lv.EVENT.KEY, None)
        group = lv.group_get_default()
        if group:
            group.add_obj(screen)
        self._corner_clock = None
        if self.show_corner_clock:
            self._corner_clock = self._make_corner_clock(screen)
        self._show_page(self.current_page)

    def _show_page(self, index):
        """Reveal a page, building it the first time it is asked for."""
        for i, cont in self._page_conts.items():
            if i != index:
                cont.add_flag(lv.obj.FLAG.HIDDEN)

        cont = self._page_conts.get(index)
        if cont is None:
            cont = self._build_page(index)
            self._page_conts[index] = cont
        cont.remove_flag(lv.obj.FLAG.HIDDEN)
        # Chrome (dots, cog) is a sibling created first, so keep the page
        # behind it rather than on top.
        cont.move_background()

        self._tile_labels = self._page_tiles[index]
        # Only re-render if data actually moved while this page was hidden;
        # otherwise the widgets already show the right thing and a refresh
        # is pure latency on the swipe.
        if self._page_version.get(index) != self._data_version:
            self._refresh_tiles()
            self._page_version[index] = self._data_version
        elif "clock" in self._tile_labels:
            self._update_tile("clock")      # minutes tick regardless
        self._update_corner_clock()
        self._update_dots()

    def _build_page(self, index):
        width = DisplayMetrics.width()
        height = DisplayMetrics.height()
        cont = lv.obj(self._screen)
        cont.set_size(width, height)
        cont.set_pos(0, 0)
        cont.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        cont.set_style_border_width(0, lv.PART.MAIN)
        cont.set_style_radius(0, lv.PART.MAIN)
        cont.set_style_pad_all(0, lv.PART.MAIN)
        cont.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        cont.remove_flag(lv.obj.FLAG.SCROLLABLE)
        cont.remove_flag(lv.obj.FLAG.CLICKABLE)

        fields = self.screens[index]
        n = len(fields)
        cols = 1 if n <= 2 else 2
        rows = (n + cols - 1) // cols
        pad = max(4, width // 60)
        tile_w = (width - pad * (cols + 1)) // cols
        tile_h = (height - self._dots_space - pad * (rows + 1)) // rows

        tiles = {}
        previous, self._tile_labels = self._tile_labels, tiles
        for i, field_id in enumerate(fields):
            col = i % cols
            row = i // cols
            x = pad + col * (tile_w + pad)
            y = pad + row * (tile_h + pad)
            # Last tile of an odd count in a 2-col grid spans the full row.
            w = tile_w
            if cols == 2 and i == n - 1 and n % 2 == 1:
                w = width - 2 * pad
            self._make_tile(cont, field_id, x, y, w, tile_h, rows)
        self._page_tiles[index] = tiles
        self._tile_labels = previous
        return cont

    def _make_tile(self, parent, field_id, x, y, w, h, rows):
        tile = lv.obj(parent)
        tile.set_pos(x, y)
        tile.set_size(w, h)
        tile.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        tile.set_style_border_width(0, lv.PART.MAIN)
        tile.set_style_radius(0, lv.PART.MAIN)
        tile.set_style_pad_all(2, lv.PART.MAIN)
        tile.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        tile.remove_flag(lv.obj.FLAG.SCROLLABLE)
        tile.remove_flag(lv.obj.FLAG.CLICKABLE)

        small = 10 if rows >= 3 else 12
        small_font = FontManager.getFont(size=small)

        title = None
        if self.show_titles:
            title = lv.label(tile)
            title.set_text(FIELD_TITLES.get(field_id, field_id).upper())
            title.set_style_text_font(small_font, lv.PART.MAIN)
            title.set_style_text_color(self.fg, lv.PART.MAIN)
            title.set_style_text_opa(lv.OPA._50, lv.PART.MAIN)
            title.set_style_text_letter_space(1, lv.PART.MAIN)
            title.align(lv.ALIGN.TOP_LEFT, 0, 0)

        # A chart given the whole screen has room to answer the question
        # the line itself can't: what is the price right now. Bold and at
        # full opacity so it reads as the headline against the dim chrome.
        price = None
        head = small + 4 if self.show_titles else 2
        if field_id in CHART_FIELDS and rows == 1:
            price_size = small + 8
            price = lv.label(tile)
            price.set_text("")
            price.set_style_text_font(self._value_font(price_size), lv.PART.MAIN)
            price.set_style_text_color(self.fg, lv.PART.MAIN)
            price.set_style_text_opa(lv.OPA.COVER, lv.PART.MAIN)
            price.align(lv.ALIGN.TOP_RIGHT, 0, -2)
            # The chart must start below it, or the curve runs through the
            # text — the price is taller than the title it sits beside.
            head = max(head, int(price_size * 1.3))

        if field_id in CHART_FIELDS:
            value = self._make_chart(tile, field_id, w, h, small, head)
            if price is not None:
                price.move_foreground()
        else:
            value = Odometer(tile)
            # Slightly above center so the sub label fits directly below.
            value.align(lv.ALIGN.CENTER, 0, -(small // 2 + 1))

        sub = lv.label(tile)
        sub.set_text("")
        pill_fonts = None
        if field_id in CHART_FIELDS:
            # Charts caption in bold, trend-coloured type. A move past
            # its threshold turns the same label into a filled pill (see
            # _update_chart_tile) -- sized to its content rather than the
            # tile, or that fill would span the whole row.
            pill_fonts = (self._value_font(small + PILL_BUMP),
                          self._value_font(small + PILL_BUMP_BIG))
            sub.set_style_text_font(pill_fonts[0], lv.PART.MAIN)
            sub.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
            sub.set_style_radius(lv.RADIUS_CIRCLE, lv.PART.MAIN)
            sub.set_style_pad_hor(7, lv.PART.MAIN)
            sub.set_style_pad_ver(3, lv.PART.MAIN)
        else:
            sub.set_style_text_font(small_font, lv.PART.MAIN)
            sub.set_style_text_color(self.fg, lv.PART.MAIN)
            sub.set_style_text_opa(lv.OPA._50, lv.PART.MAIN)
            sub.set_long_mode(lv.label.LONG_MODE.DOTS)
            sub.set_width(w - 8)
            # Text hugs the right edge of its box; the box itself is
            # anchored under the value's bottom-right corner on every
            # refresh (the value's size changes with its text, so
            # align_to is re-applied in _update_tile rather than here).
            sub.set_style_text_align(lv.TEXT_ALIGN.RIGHT, lv.PART.MAIN)

        # Max value font size: tile height minus title (when shown), the
        # sub label hanging under the value, and breathing room — divided
        # by the TTF's ~1.15 line-height factor so the line still fits.
        reserved = (3 * small + 10) if self.show_titles else (2 * small + 8)
        value_h = max(16, int((h - reserved) / 1.15))
        self._tile_labels[field_id] = (title, value, sub, w - 8, value_h,
                                       price, pill_fonts)

    def _make_dots(self, screen, width, height, dots_space):
        if len(self.screens) <= 1:
            return
        count = len(self.screens)
        size = 5
        gap = 8
        total = count * size + (count - 1) * gap
        x0 = (width - total) // 2
        y = height - dots_space + (dots_space - size) // 2
        for i in range(count):
            dot = lv.obj(screen)
            dot.set_size(size, size)
            dot.set_pos(x0 + i * (size + gap), y)
            dot.set_style_radius(size, lv.PART.MAIN)
            dot.set_style_border_width(0, lv.PART.MAIN)
            dot.set_style_bg_color(self.fg, lv.PART.MAIN)
            dot.set_style_bg_opa(
                lv.OPA.COVER if i == self.current_page else lv.OPA._30,
                lv.PART.MAIN)
            self._dots.append(dot)
            dot.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
            dot.remove_flag(lv.obj.FLAG.SCROLLABLE)
            dot.remove_flag(lv.obj.FLAG.CLICKABLE)

    def _make_chart(self, tile, field_id, w, h, small, top=None):
        """Bare line chart for the 24h price field: no grid, no axes, no
        point markers — just the curve, in the theme's text color.

        top is the space the caller has already claimed above the plot."""
        if top is None:
            top = small + 4 if self.show_titles else 2
        # Room for the trend pill at its LARGER size, always: reserving only
        # the small one would resize the plot the moment a move crossed its
        # emphasis threshold.
        bottom = int((small + PILL_BUMP_BIG) * 1.15) + 10
        # Sits where the plot will be, for the first fetch on a cold
        # install: an empty tile looks broken, and a chart that costs a
        # megabyte deserves to say so while it arrives.
        loading = lv.label(tile)
        loading.set_text("")
        loading.set_style_text_font(FontManager.getFont(size=small), lv.PART.MAIN)
        loading.set_style_text_color(self.fg, lv.PART.MAIN)
        loading.set_style_text_opa(lv.OPA._60, lv.PART.MAIN)
        loading.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)
        loading.set_width(w - 16)
        loading.align(lv.ALIGN.CENTER, 0, (top - bottom) // 2)
        loading.add_flag(lv.obj.FLAG.HIDDEN)
        self._chart_loading[field_id] = loading

        chart = lv.chart(tile)
        chart.set_size(w - 8, max(20, h - top - bottom))
        chart.align(lv.ALIGN.TOP_LEFT, 0, top)
        chart.set_type(lv.chart.TYPE.LINE)
        chart.set_point_count(24)   # replaced with the real count on update
        chart.set_div_line_count(0, 0)
        chart.set_update_mode(lv.chart.UPDATE_MODE.SHIFT)
        chart.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        chart.set_style_border_width(0, lv.PART.MAIN)
        chart.set_style_pad_all(0, lv.PART.MAIN)
        chart.set_style_radius(0, lv.PART.MAIN)
        chart.set_style_line_width(2, lv.PART.ITEMS)
        chart.set_style_size(0, 0, lv.PART.INDICATOR)   # no dots on points
        chart.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        chart.remove_flag(lv.obj.FLAG.SCROLLABLE)
        chart.remove_flag(lv.obj.FLAG.CLICKABLE)
        # The handle this returns must NOT be cached: set_point_count()
        # reallocates the series' sample array, which leaves a held handle
        # pointing at the old one. Writes through the stale handle land
        # nowhere the chart draws from — the symptom was a 24-hour line
        # that only ever drew its first 24 points, about a fifth of the
        # tile. _chart_live_series() re-reads it from the chart instead.
        chart.add_series(self.fg, lv.chart.AXIS.PRIMARY_Y)
        return chart

    @staticmethod
    def _chart_live_series(chart):
        """The series the chart is actually drawing right now. There is
        exactly one per chart, so the head of the list is it."""
        return chart.get_series_next(None)

    def _update_chart_tile(self, field_id):
        (title, chart, sub, _max_w, _max_h, price,
         pill_fonts) = self._tile_labels[field_id]
        value_text, _range_text = render_field(field_id, self.state)
        # The percentage alone: the range and currency are already on the
        # title and (full screen) in the price above.
        sub.set_text(value_text)
        sub.align(lv.ALIGN.BOTTOM_RIGHT, 0, 0)

        if price is not None:              # full-screen charts only
            spot, currency = render_field("spot_price", self.state)
            price.set_text("{} {}".format(spot, currency))
            price.align(lv.ALIGN.TOP_RIGHT, 0, -2)

        points = chart_series(field_id, self.state)
        # Read off the full series, not the thinned one drawn below, and
        # rounded the way the pill prints it so the number shown and the
        # size it is shown at agree.
        pct = 0.0
        if len(points) >= 2 and points[0]:
            pct = round((points[-1] - points[0]) / points[0] * 100.0, 1)
        rising = True
        if len(points) >= 2:
            # One point per pixel column is the display's ceiling; thin
            # only past that. No smoothing — every plotted value is a
            # real sample (or, on a cold 24h chart, the straight line
            # between two real hourly samples).
            width = max(24, chart.get_width())
            points = _decimate(points, width)
            lo, hi = min(points), max(points)
            if hi == lo:                    # flat line: give it room to sit mid-tile
                lo, hi = lo - 1, hi + 1
            pad = (hi - lo) * 0.12          # keep the curve off the edges
            chart.set_axis_range(lv.chart.AXIS.PRIMARY_Y,
                                 int(lo - pad), int(hi + pad) + 1)
            chart.set_point_count(len(points))
            # Fetched after set_point_count, never before: the realloc is
            # exactly what invalidates an older handle.
            series = self._chart_live_series(chart)
            rising = points[-1] >= points[0]
            chart.set_series_color(
                series, lv.color_hex(self._trend_up if rising else self._trend_down))
            values = [int(v) for v in points]
            try:
                # One call beats 144 round-trips into LVGL on the ESP32.
                chart.set_series_values(series, values, len(values))
            except Exception:
                for i, sample in enumerate(values):
                    chart.set_series_value_by_id(series, i, sample)
            chart.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            chart.add_flag(lv.obj.FLAG.HIDDEN)

        self._apply_title_color(field_id)
        if len(points) >= 2:
            # Staleness outranks the trend: a warning colour fills the pill
            # instead of green/red, so a late chart cannot pass itself off
            # as a confident reading.
            stale = self._stale_color(field_id)
            fill = stale if stale is not None else (
                self._trend_up if rising else self._trend_down)
            # The pill is the signal, not the default dress: an ordinary
            # move is bold coloured text, and only one past its threshold
            # earns the filled badge (and the larger type with it). That
            # way the badge means something when you see it.
            threshold = PCT_EMPHASIS.get(CHART_LABELS.get(field_id))
            big = (pill_fonts is not None and threshold is not None
                   and abs(pct) > threshold)
            if pill_fonts is not None:
                sub.set_style_text_font(pill_fonts[1] if big else pill_fonts[0],
                                        lv.PART.MAIN)
            sub.set_style_bg_color(lv.color_hex(fill), lv.PART.MAIN)
            sub.set_style_bg_opa(lv.OPA.COVER if big else lv.OPA.TRANSP,
                                 lv.PART.MAIN)
            sub.set_style_text_color(
                lv.color_hex(_knockout(fill) if big else fill), lv.PART.MAIN)
            sub.set_style_text_opa(lv.OPA.COVER, lv.PART.MAIN)
            sub.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            # Nothing plotted, nothing to badge — the chart is hidden too.
            sub.add_flag(lv.obj.FLAG.HIDDEN)
        self._update_chart_loading(field_id, len(points) >= 2)

    def _update_chart_loading(self, field_id, has_data):
        """Show what the first fetch is doing, or get out of the way."""
        label = self._chart_loading.get(field_id)
        if label is None:
            return
        try:
            if has_data:
                label.add_flag(lv.obj.FLAG.HIDDEN)
                return
            label.set_text(self._loading_text(field_id))
            label.remove_flag(lv.obj.FLAG.HIDDEN)
        except Exception:
            self._chart_loading.pop(field_id, None)   # widget went away

    def _loading_text(self, field_id):
        """Bytes and rate, not a percentage: the feed is hourly for its
        first stretch and daily beyond, so how much is left to read is
        genuinely unknown until it arrives. A number that moves is honest;
        a percentage would be invented."""
        label = CHART_LABELS.get(field_id, "24h")
        done, rate = self._history_bytes, self._history_rate
        if not self._history_active or done <= 0:
            return "Loading {} chart...".format(label)
        got = "{:.0f} KB".format(done / 1024.0)
        if rate > 0:
            return "Loading {} chart\n{} at {:.0f} KB/s".format(label, got, rate / 1024.0)
        return "Loading {} chart\n{}".format(label, got)

    def _history_progress(self, done):
        """Called from the download as chunks land."""
        now = time.time()
        elapsed = now - self._history_started
        self._history_bytes = done
        self._history_rate = done / elapsed if elapsed > 0.2 else 0
        for field_id in self._tile_labels:
            if field_id in CHART_FIELDS and field_id in self._chart_loading:
                if len(chart_series(field_id, self.state)) < 2:
                    self._update_chart_loading(field_id, False)

    def _make_corner_clock(self, screen):
        """Small always-on clock and date, bottom-left. Part of the chrome,
        not a tile, so it stays put while pages turn. It shares the bottom
        edge with the page dots (centred) and the cog (right), and is short
        enough to clear both."""
        label = lv.label(screen)
        label.set_text("")
        label.set_style_text_font(FontManager.getFont(size=12), lv.PART.MAIN)
        label.set_style_text_color(self.fg, lv.PART.MAIN)
        label.set_style_text_opa(lv.OPA._50, lv.PART.MAIN)
        label.align(lv.ALIGN.BOTTOM_LEFT, 3, -CLOCK_PAD)
        return label

    def _update_corner_clock(self):
        if self._corner_clock is None:
            return
        lt = self.state.get("localtime")
        if not lt:
            return
        clock = "%02d:%02d" % (lt[3], lt[4])
        try:
            text = "%s %d %s | %s" % (WEEKDAYS[lt[6]], lt[2],
                                      MONTHS[lt[1] - 1], clock)
        except (IndexError, TypeError):
            text = clock                   # time alone if the date is odd
        try:
            self._corner_clock.set_text(text)
        except Exception:
            self._corner_clock = None      # widget went away with a rebuild

    def _update_dots(self):
        for i, dot in enumerate(self._dots):
            try:
                dot.set_style_bg_opa(
                    lv.OPA.COVER if i == self.current_page else lv.OPA._30,
                    lv.PART.MAIN)
            except Exception:
                pass

    def _make_settings_button(self, screen):
        btn = lv.button(screen)
        btn.set_size(30, 30)
        # Dropped so the icon's centre lands on the corner clock's text
        # line rather than sitting above it: the clock's centre is
        # CLOCK_PAD + half a line off the bottom, the icon's is half the
        # button. The button's own box may hang past the screen edge --
        # it is transparent, and the hit area is grown separately.
        btn.align(lv.ALIGN.BOTTOM_RIGHT, 1,
                  15 - (CLOCK_PAD + CLOCK_LINE // 2))
        btn.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        btn.set_style_border_width(0, lv.PART.MAIN)
        btn.set_style_shadow_width(0, lv.PART.MAIN)
        # The cog is drawn small and faint on purpose, but a 30 px target
        # is a poor one for a fingertip, so the hit area is grown well
        # beyond the artwork without changing how it looks.
        btn.set_ext_click_area(SETTINGS_HIT_PAD)
        btn.add_event_cb(self._open_settings, lv.EVENT.CLICKED, None)
        # On a keypad-only device the cog is the sole focusable thing on the
        # dashboard, so focus lands here -- which also makes it the widget
        # that receives arrow keys for paging.
        add_focus_border(btn)
        btn.add_event_cb(self._on_key, lv.EVENT.KEY, None)
        icon = lv.label(btn)
        icon.set_text(lv.SYMBOL.SETTINGS)
        icon.set_style_text_color(self.fg, lv.PART.MAIN)
        icon.set_style_text_opa(lv.OPA._40, lv.PART.MAIN)
        icon.center()

    # --- Tile refresh ---

    def _update_tile(self, field_id):
        if field_id in CHART_FIELDS:
            self._update_chart_tile(field_id)
            return
        (title, value, sub, max_w, max_h,
         _price, _pill_fonts) = self._tile_labels[field_id]
        value_text, sub_text = render_field(field_id, self.state)
        font = self._value_font(fit_size(value_text, max_w, max_h))
        # Digits roll odometer-style; shape changes rebuild instantly.
        value.set_value(value_text, font, self.fg,
                        animate=self.has_foreground())
        sub.set_text(sub_text or "")
        # Pin the unit/sub text just under the value's bottom-right corner.
        value.cont.update_layout()
        sub.align_to(value.cont, lv.ALIGN.OUT_BOTTOM_RIGHT, 0, 2)
        self._apply_title_color(field_id)

    def _refresh_tiles(self):
        for field_id in self._tile_labels:
            self._update_tile(field_id)

    def _refresh_clock(self):
        try:
            self.state["localtime"] = mpos.time.localtime()
        except Exception:
            self.state["localtime"] = time.localtime()

    def _clock_tick(self, timer):
        if not self.has_foreground():
            return
        self._refresh_clock()
        self._update_corner_clock()
        if "clock" in self._tile_labels:
            self._update_tile("clock")
        if time.time() - self._stale_check_at >= 30:
            self._stale_check_at = time.time()
            self._check_staleness()
        if self._splash and self._splash_until and time.time() >= self._splash_until:
            self._hide_splash()

    # --- Market data ---

    async def _market_loop(self, gen):
        while self.keep_running and gen == self._loop_gen:
            old = (self.state.get("height"), self.state.get("price"),
                   self.state.get("fee"))
            try:
                await self.market.fetch(
                    self.state, self.state["currency"],
                    want_priority_fees=self._uses_priority_fees())
            except Exception as e:
                print("BlockTV: market fetch error: {}".format(e))
            if self.keep_running and gen == self._loop_gen:
                # Refresh the hourly feed first: after a restart it is what
                # lets record_fine fill the missed slots with real prices
                # instead of a straight line.
                await self._maybe_fetch_history()
                # Tolerance for "is this price a fresh observation?": a
                # slot, or two poll intervals if the user has slowed the
                # poll down past that, so a slow-but-working feed is not
                # mistaken for a dead one.
                record_fine(self.state,
                            max(FINE_SLOT_SECONDS, 2 * self.refresh_seconds))
                extend_series(self.state)
            if self.keep_running and gen == self._loop_gen:
                self._data_version += 1
            if self.keep_running and gen == self._loop_gen and self.has_foreground():
                self._refresh_tiles()
                self._page_version[self.current_page] = self._data_version
                self._maybe_flash(old)
            if (time.time() - self._cache_saved_at >= CACHE_SAVE_SECONDS
                    and self.state.get("height") is not None):
                self._save_cache()
            slept = 0
            while self.keep_running and gen == self._loop_gen and slept < self.refresh_seconds:
                await TaskManager.sleep(1)
                slept += 1

    # --- Start screen ---

    def _show_logo_splash(self, screen=None):
        """Brand splash over the freshly-built dashboard. Auto-dismisses;
        tapping skips it.

        `screen` must be this activity's own screen: during onResume the
        active screen can still be the launcher's (the transition has not
        finished), and parenting the overlay there leaves it dangling when
        that screen is destroyed — which segfaults on the next redraw."""
        if screen is None:
            screen = lv.screen_active()
        overlay = lv.obj(screen)
        overlay.set_size(lv.pct(100), lv.pct(100))
        overlay.set_pos(0, 0)
        overlay.set_style_bg_color(self.bg, lv.PART.MAIN)
        overlay.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        overlay.set_style_border_width(0, lv.PART.MAIN)
        overlay.set_style_radius(0, lv.PART.MAIN)
        overlay.set_style_pad_all(0, lv.PART.MAIN)
        overlay.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        overlay.remove_flag(lv.obj.FLAG.SCROLLABLE)
        overlay.add_flag(lv.obj.FLAG.CLICKABLE)
        # Tapping skips the splash — but not for the first moments. The
        # launcher's own icon splash means the app appears under a finger
        # that is often still down, and MPOS's gesture navigation replays
        # that release as a click on whatever screen is now active, which
        # would dismiss the logo the instant it appeared.
        self._logo_tap_ok_at = time.time() + LOGO_TAP_GRACE
        overlay.add_event_cb(lambda e: self._logo_tapped(),
                             lv.EVENT.CLICKED, None)

        # Ink follows the background: inverted (white) artwork on dark
        # themes, the logo as drawn on light ones. The gold is identical
        # in both.
        asset = (LOGO_ASSET_LIGHT if _lum(self._bg_hex) > 140
                 else LOGO_ASSET_DARK)
        path = resolve_drawable(self.appFullName, asset)
        shown = False
        if path:
            try:
                img = lv.image(overlay)
                img.set_src(path)
                img.center()
                img.update_layout()
                shown = img.get_width() > 0
                if shown:
                    self._logo_image = img
                else:
                    img.set_src(None)
                    img.delete()
            except Exception as e:
                print("BlockTV: logo image failed: {}".format(e))
        if not shown:
            # Wordmark fallback keeps the start screen branded even if the
            # PNG is missing or the decoder rejects it.
            label = lv.label(overlay)
            label.set_text("BlockTV!")
            label.set_style_text_color(self.fg, lv.PART.MAIN)
            label.set_style_text_font(
                self._value_font(fit_size("BlockTV!",
                                          DisplayMetrics.width() * 4 // 5,
                                          DisplayMetrics.height() // 4)),
                lv.PART.MAIN)
            label.center()

        self._logo_overlay = overlay
        timer = lv.timer_create(
            lambda _t: self._hide_logo_splash(), LOGO_SPLASH_MS, None)
        timer.set_repeat_count(1)   # one-shot; auto-deletes after firing

    def _logo_tapped(self):
        if time.time() < getattr(self, "_logo_tap_ok_at", 0):
            return      # stray release from the launch tap, not a skip
        self._hide_logo_splash()

    def _hide_logo_splash(self):
        """Hide immediately, free later. Deleting the decoded image from
        inside the dismiss timer runs while LVGL is mid-frame and corrupts
        the PNG decoder's cache (crashes on the next decode), so the
        teardown is deferred to the event loop via async_call."""
        overlay = self._logo_overlay
        if overlay is None:
            return
        self._logo_overlay = None
        try:
            overlay.add_flag(lv.obj.FLAG.HIDDEN)
        except Exception:
            return

        def _destroy(*_args, o=overlay):
            try:
                if self._logo_image is not None:
                    self._logo_image.set_src(None)
                o.delete()
            except Exception:
                pass
            self._logo_image = None

        try:
            lv.async_call(_destroy, None)
        except Exception:
            _destroy()

    def _configured_ranges(self):
        """Chart ranges actually in use, shortest first."""
        labels = set()
        for screen in self.screens:
            for field_id in screen:
                if field_id in CHART_FIELDS:
                    labels.add(CHART_LABELS.get(field_id, "24h"))
        return sorted(labels, key=lambda lb: RANGE_SPECS[lb][0])

    async def _maybe_fetch_history(self):
        """Refresh chart data for the ranges actually on screen.

        Each range has its own cadence, scaled to what it costs to reach
        (a day is ~1 KB, a year ~276 KB). Because the feed is newest-first,
        fetching a long range also refreshes every shorter one, so a due
        long range subsumes the short ones in a single pass."""
        labels = self._configured_ranges()
        if not labels:
            return
        currency = self.state.get("currency", "USD")
        now = time.time()
        fetched = self.state.setdefault("fetched_at", {})
        due = [lb for lb in labels
               if now - fetched.get(lb, 0) >= RANGE_REFRESH[lb]]
        if not due:
            return

        longest = max(due, key=lambda lb: RANGE_SPECS[lb][0])
        horizon = RANGE_SPECS[longest][0]
        # One pass fills every configured range within that horizon.
        covered = [lb for lb in labels if RANGE_SPECS[lb][0] <= horizon]
        self._history_active = True
        self._history_started = time.time()
        self._history_bytes = 0
        self._history_rate = 0
        if self.has_foreground():
            self._refresh_tiles()          # paint "Loading..." before the wait
        try:
            if await self.market.fetch_history(self.state, currency, covered,
                                               progress=self._history_progress):
                for lb in covered:
                    fetched[lb] = now
                self._data_version += 1
                if self.has_foreground():
                    self._refresh_tiles()
        except Exception as e:
            print("BlockTV: history refresh error: {}".format(e))
        finally:
            self._history_active = False
            if self.has_foreground():
                self._refresh_tiles()

    # --- Attention flash ---

    def _maybe_flash(self, old):
        """Flash the screen if a watched value changed (per flash_mode).
        Initial population (None -> value) never flashes."""
        if self.flash_mode == "off":
            return
        old_height, old_price, old_fee = old
        if self.flash_mode == "block":
            changed = (old_height is not None
                       and self.state.get("height") != old_height)
        else:  # "any" — clock excluded by design or it would flash every minute
            changed = (
                (old_height is not None and self.state.get("height") != old_height)
                or (old_price is not None and self.state.get("price") != old_price)
                or (old_fee is not None and self.state.get("fee") != old_fee))
        if changed:
            self._flash()

    def _flash(self):
        if not self.has_foreground():
            return
        screen = lv.screen_active()
        overlay = lv.obj(screen)
        overlay.set_size(lv.pct(100), lv.pct(100))
        overlay.set_pos(0, 0)
        overlay.set_style_bg_color(self.fg, lv.PART.MAIN)
        overlay.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        overlay.set_style_border_width(0, lv.PART.MAIN)
        overlay.set_style_radius(0, lv.PART.MAIN)
        overlay.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        overlay.remove_flag(lv.obj.FLAG.SCROLLABLE)
        overlay.remove_flag(lv.obj.FLAG.CLICKABLE)

        def _end(_timer, o=overlay):
            try:
                o.delete()
            except Exception:
                pass

        timer = lv.timer_create(_end, FLASH_MS, None)
        timer.set_repeat_count(1)  # one-shot; auto-deletes after firing

    # --- Zap / NWC callbacks (run on the shared asyncio loop) ---

    def _on_zap(self, zap, live):
        self.state["zap"] = zap
        self.state.setdefault("updated_at", {})["nostr"] = time.time()
        self.update_ui_threadsafe_if_foreground(self._zap_ui_update, zap, live)

    def _zap_ui_update(self, zap, live):
        self._data_version += 1
        self._refresh_tiles()
        self._page_version[self.current_page] = self._data_version
        if live and self.zap_splash_enabled:
            self._show_splash(zap)

    def _on_balance(self, balance):
        changed = (self.state.get("balance") is not None
                   and balance != self.state.get("balance"))
        self.state["balance"] = balance
        self.state.setdefault("updated_at", {})["nwc"] = time.time()
        self._data_version += 1
        self.update_ui_threadsafe_if_foreground(self._refresh_tiles)
        if changed and self.flash_mode == "any":
            self.update_ui_threadsafe_if_foreground(self._flash)

    # --- Zap splash ---

    def _show_splash(self, zap):
        screen = lv.screen_active()
        self._hide_splash()
        splash = lv.obj(screen)
        splash.set_size(lv.pct(100), lv.pct(100))
        splash.set_pos(0, 0)
        splash.set_style_radius(0, lv.PART.MAIN)
        splash.set_style_border_width(0, lv.PART.MAIN)
        # Inverted colors so a zap visibly flips the whole display.
        splash.set_style_bg_color(self.fg, lv.PART.MAIN)
        splash.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        splash.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        splash.remove_flag(lv.obj.FLAG.SCROLLABLE)
        splash.add_event_cb(lambda e: self._hide_splash(), lv.EVENT.CLICKED, None)

        bolt = lv.label(splash)
        bolt.set_text(lv.SYMBOL.CHARGE)
        bolt.set_style_text_color(self.bg, lv.PART.MAIN)
        bolt.set_style_text_font(FontManager.getFont(size=28), lv.PART.MAIN)
        bolt.align(lv.ALIGN.CENTER, 0, -40)

        amount = lv.label(splash)
        sats = zap.get("sats")
        from fields import fmt_int
        amount_text = "+{} sats".format(fmt_int(sats)) if sats is not None else "zap!"
        amount.set_text(amount_text)
        amount.set_style_text_color(self.bg, lv.PART.MAIN)
        amount.set_style_text_font(
            self._value_font(fit_size(
                amount_text,
                DisplayMetrics.width() * 9 // 10,
                DisplayMetrics.height() // 4)),
            lv.PART.MAIN)
        amount.align(lv.ALIGN.CENTER, 0, 0)

        comment = zap.get("comment") or ""
        if comment:
            comment_label = lv.label(splash)
            comment_label.set_text(comment)
            comment_label.set_style_text_color(self.bg, lv.PART.MAIN)
            comment_label.set_style_text_font(FontManager.getFont(size=14), lv.PART.MAIN)
            comment_label.set_long_mode(lv.label.LONG_MODE.DOTS)
            comment_label.set_width(lv.pct(90))
            comment_label.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)
            comment_label.align(lv.ALIGN.CENTER, 0, 40)

        self._splash = splash
        self._splash_until = time.time() + SPLASH_SECONDS

    def _hide_splash(self):
        if self._splash:
            try:
                self._splash.delete()
            except Exception:
                pass
            self._splash = None
            self._splash_until = 0

    # --- Navigation ---

    def _on_pressed(self, event):
        indev = lv.indev_active()
        if not indev:
            return
        point = lv.point_t()
        indev.get_point(point)
        self._press_x = point.x
        self._press_y = point.y
        self._swipe_done = False

    def _swipe_delta(self):
        indev = lv.indev_active()
        if not indev:
            return None
        point = lv.point_t()
        indev.get_point(point)
        return point.x - self._press_x, point.y - self._press_y

    def _try_swipe(self):
        """Turn the page as soon as the finger has travelled far enough,
        rather than waiting for it to lift — the rebuild takes long enough
        on-device that waiting for the release makes the gesture feel
        ignored."""
        if self._swipe_done or len(self.screens) < 2:
            return
        delta = self._swipe_delta()
        if delta is None:
            return
        dx, dy = delta
        if abs(dx) >= SWIPE_THRESHOLD and abs(dx) > SWIPE_RATIO * abs(dy):
            self._swipe_done = True
            self._turn_page(1 if dx < 0 else -1)

    def _turn_page(self, step):
        self.current_page = (self.current_page + step) % len(self.screens)
        self._show_page(self.current_page)

    def _on_pressing(self, event):
        self._try_swipe()

    def _on_released(self, event):
        self._try_swipe()      # catches quick flicks that send few PRESSING events

    # --- Physical button ---

    def _start_button_watcher(self):
        """Watch the BOOT button (GPIO0) as a second way to page through
        screens — useful when the display is mounted out of reach or the
        touch panel is awkward. Absent on desktop builds, where there is
        no such pin; that is not an error."""
        if self._button_task is not None:
            return
        try:
            from machine import Pin
            self._button_pin = Pin(BUTTON_GPIO, Pin.IN, Pin.PULL_UP)
        except Exception as e:
            print("BlockTV: no physical button available ({})".format(e))
            self._button_pin = None
            return
        self._button_task = TaskManager.create_task(self._button_watcher())

    async def _button_watcher(self):
        """Poll rather than IRQ: presses are rare, the loop is cheap, and
        it stops cleanly with the rest of the app."""
        pin = self._button_pin
        while self.keep_running:
            try:
                if pin.value() == 0:                     # active low
                    await TaskManager.sleep(BUTTON_POLL_S)
                    if pin.value() == 0:                 # debounced press
                        while self.keep_running and pin.value() == 0:
                            await TaskManager.sleep(BUTTON_POLL_S)
                        if self.keep_running and self.has_foreground():
                            if self._logo_overlay is not None:
                                self._hide_logo_splash()
                            elif len(self.screens) > 1:
                                self._turn_page(1)
            except Exception as e:
                print("BlockTV: button watcher error: {}".format(e))
                await TaskManager.sleep(1)
            await TaskManager.sleep(BUTTON_POLL_S)
        self._button_task = None

    def _uses_priority_fees(self):
        """Whether any screen shows a low/high priority fee. They come from
        a second endpoint, so it is only worth asking for when shown."""
        for fields in self.screens:
            for field_id in fields:
                if field_id in PRIORITY_FEE_FIELDS:
                    return True
        return False

    def _on_key(self, event):
        """Arrow keys turn pages on a device with no touchscreen.

        The board's keypad driver delivers the key to whatever holds
        focus and separately moves focus itself, so this only has to
        answer the key -- there is nothing to swallow."""
        try:
            key = event.get_key()
        except Exception:
            return
        if self._logo_overlay is not None:
            self._logo_tapped()        # any key skips the start screen
            return
        if len(self.screens) < 2:
            return
        if key == lv.KEY.LEFT:
            self._turn_page(-1)
        elif key == lv.KEY.RIGHT:
            self._turn_page(1)

    def _open_settings(self, event):
        intent = Intent(activity_class=MainSettingsActivity)
        intent.putExtra("prefs", self.prefs)
        self.startActivity(intent)


class MainSettingsActivity(SettingsActivity):

    def _refresh_placeholder(self):
        """Activity rows show their placeholder as the value text, so the
        interval has to be written into it before the list renders."""
        for setting in self.settings:
            if setting.get("key") == "refresh_seconds":
                secs = self.prefs.get_string("refresh_seconds")
                try:
                    secs = int(secs)
                except (TypeError, ValueError):
                    secs = DEFAULT_REFRESH_SECONDS
                setting["placeholder"] = "Every {} seconds".format(secs)

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        self.settings = [
            {"title": "Screens", "ui": "activity",
             "activity_class": ScreensSettingsActivity,
             "placeholder": "Compose your data screens", "key": "screens_json"},
            {"title": "Customise", "ui": "activity",
             "activity_class": CustomiseSettingsActivity,
             "placeholder": "Colors, titles, effects", "key": "_customise"},
            {"title": "Nostr", "ui": "activity",
             "activity_class": NostrSettingsActivity,
             "placeholder": "Zaps, relays, wallet connect", "key": "_nostr"},
            {"title": "Currency", "key": "currency", "ui": "dropdown",
             "default_value": "USD",
             "ui_options": [(c, c) for c in CURRENCIES]},
            {"title": "Refresh Interval", "ui": "activity",
             "activity_class": RefreshSettingsActivity,
             "placeholder": "", "key": "refresh_seconds"},
            {"title": "API Server", "key": "mempool_url",
             "placeholder": DEFAULT_BASE_URL, "default_value": DEFAULT_BASE_URL},
            {"title": "About", "ui": "activity",
             "activity_class": AboutActivity,
             "placeholder": "Versions and where to find us", "key": "_about"},
        ]
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)

    def onResume(self, screen):
        # SettingsActivity rebuilds the list on every resume, so the
        # floating return button has to be re-added after it.
        self._refresh_placeholder()
        super().onResume(screen)
        _add_floating_back(screen, self.finish)


class RefreshSettingsActivity(Activity):
    """How often to poll, on its own screen.

    MPOS reserves the leftmost 24 px for the back-swipe gesture, and the
    OS settings slider spans 90% of the width — so its low end sits under
    that strip, and grabbing it there and dragging right navigates back
    instead of setting a value. This slider is 60% wide and centred, which
    starts it at x=64, clear of the strip by a wide margin."""

    SLIDER_PCT = 60
    MIN_SECONDS = 15
    MAX_SECONDS = 600

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        self._pending = None
        self._value_label = None
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                              lv.FLEX_ALIGN.CENTER)
        screen.set_style_pad_row(10, lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)

    def _stored(self):
        try:
            return max(self.MIN_SECONDS,
                       min(self.MAX_SECONDS,
                           int(self.prefs.get_string("refresh_seconds"))))
        except (TypeError, ValueError):
            return DEFAULT_REFRESH_SECONDS

    def onResume(self, screen):
        super().onResume(screen)
        screen.clean()
        current = self._stored()
        self._pending = current

        title = lv.label(screen)
        title.set_text("Refresh Interval")
        title.set_style_text_font(FontManager.getFont(size=18), lv.PART.MAIN)

        self._value_label = lv.label(screen)
        self._value_label.set_style_text_font(FontManager.getFont(size=24),
                                              lv.PART.MAIN)
        self._show(current)

        slider = lv.slider(screen)
        slider.set_range(self.MIN_SECONDS, self.MAX_SECONDS)
        slider.set_value(current, False)
        slider.set_width(lv.pct(self.SLIDER_PCT))
        slider.add_event_cb(lambda e: self._changed(slider),
                            lv.EVENT.VALUE_CHANGED, None)
        # Focusable so a keypad can reach it; LVGL sliders take LEFT/RIGHT.
        add_focus_border(slider)

        hint = lv.label(screen)
        hint.set_text("How often block height, price and fees are fetched.")
        hint.set_style_text_font(FontManager.getFont(size=12), lv.PART.MAIN)
        hint.set_style_text_opa(lv.OPA._60, lv.PART.MAIN)
        hint.set_long_mode(lv.label.LONG_MODE.WRAP)
        hint.set_width(lv.pct(90))
        hint.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)

        _add_floating_back(screen, self.finish)

    def _show(self, seconds):
        if self._value_label is not None:
            self._value_label.set_text("{} s".format(seconds))

    def _changed(self, slider):
        # Held in memory while dragging; committing on every tick would
        # write to flash dozens of times per swipe.
        self._pending = slider.get_value()
        self._show(self._pending)

    def onPause(self, screen):
        if self._pending is not None and self._pending != self._stored():
            editor = self.prefs.edit()
            editor.put_string("refresh_seconds", str(self._pending))
            editor.commit()
        super().onPause(screen)


class NostrSettingsActivity(SettingsActivity):
    """Everything nostr: zap watching and wallet connect."""

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        self.settings = [
            {"title": "Nostr Zaps: npub", "key": "zap_npub",
             "placeholder": "npub1... or hex pubkey"},
            {"title": "Nostr Relays", "key": "zap_relays",
             "placeholder": DEFAULT_RELAYS, "default_value": DEFAULT_RELAYS},
            {"title": "Nostr Wallet Connect", "key": "nwc_url",
             "placeholder": "nostr+walletconnect://..."},
        ]
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)

    def onResume(self, screen):
        # SettingsActivity rebuilds the list on every resume, so the
        # floating return button has to be re-added after it.
        super().onResume(screen)
        _add_floating_back(screen, self.finish)


class CustomiseSettingsActivity(SettingsActivity):
    """Everything about how the display looks and behaves."""

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        self.settings = [
            {"title": "Text Color", "key": "fg_color", "ui": "radiobuttons",
             "default_value": DEFAULT_FG, "ui_options": COLOR_OPTIONS},
            {"title": "Background Color", "key": "bg_color", "ui": "radiobuttons",
             "default_value": DEFAULT_BG, "ui_options": COLOR_OPTIONS},
            {"title": "Field Titles", "key": "show_titles", "ui": "radiobuttons",
             "default_value": "on",
             "ui_options": [("Show", "on"), ("Hide", "off")]},
            {"title": "Corner Clock", "key": "corner_clock", "ui": "radiobuttons",
             "default_value": "off",
             "ui_options": [("Show time and date, bottom left", "on"),
                            ("Off", "off")]},
            {"title": "Zap Splash", "key": "zap_splash", "ui": "radiobuttons",
             "default_value": "on",
             "ui_options": [("Show fullscreen zap alert", "on"), ("Off", "off")]},
            {"title": "Screen Flash", "key": "flash_mode", "ui": "radiobuttons",
             "default_value": "off",
             "ui_options": [
                 ("Off", "off"),
                 ("New block found", "block"),
                 ("Any value change", "any"),
             ]},
        ]
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)

    def onResume(self, screen):
        # SettingsActivity rebuilds the list on every resume, so the
        # floating return button has to be re-added after it.
        super().onResume(screen)
        _add_floating_back(screen, self.finish)


class AboutActivity(Activity):
    """Logo, the three version numbers worth quoting in a bug report,
    and where to find the app."""

    def onCreate(self):
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER,
                              lv.FLEX_ALIGN.START)
        screen.set_style_pad_row(6, lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)

    def onResume(self, screen):
        super().onResume(screen)
        screen.clean()
        self._add_logo(screen)
        for name, value in (("BlockTV!", app_version(self.appFullName)),
                            ("MicroPythonOS", _os_version()),
                            ("Hardware", _pretty_hardware(_hardware_id()))):
            self._add_fact(screen, name, value)

        site = lv.label(screen)
        site.set_text(APP_SITE)
        site.set_style_text_font(FontManager.getFont(size=18), lv.PART.MAIN)
        site.set_style_pad_top(8, lv.PART.MAIN)

        _add_floating_back(screen, self.finish)

    def _add_logo(self, screen):
        # Settings screens wear the OS theme rather than the app's colours,
        # so the ink is chosen from the theme's own background, not from
        # the user's background pref.
        asset = LOGO_ASSET_DARK
        try:
            c = screen.get_style_bg_color(lv.PART.MAIN)
            if _lum((c.red << 16) | (c.green << 8) | c.blue) > 140:
                asset = LOGO_ASSET_LIGHT
        except Exception:
            pass
        path = resolve_drawable(self.appFullName, asset)
        if path:
            try:
                img = lv.image(screen)
                img.set_src(path)
                img.update_layout()
                if img.get_width() > 0:
                    return
                img.set_src(None)
                img.delete()
            except Exception as e:
                print("BlockTV: about logo failed: {}".format(e))
        label = lv.label(screen)
        label.set_text("BlockTV!")
        label.set_style_text_font(FontManager.getFont(size=28), lv.PART.MAIN)

    def _add_fact(self, screen, name, value):
        row = _button_row(screen)
        row.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER,
                           lv.FLEX_ALIGN.CENTER)
        left = lv.label(row)
        left.set_text(name)
        left.set_style_text_font(FontManager.getFont(size=14), lv.PART.MAIN)
        left.set_style_text_opa(lv.OPA._60, lv.PART.MAIN)
        right = lv.label(row)
        right.set_text(value)
        right.set_style_text_font(FontManager.getFont(size=14), lv.PART.MAIN)
        # Board names run long (Waveshare ESP32 S3 Touch LCD 2); wrap rather
        # than clip, since a half-shown board name is no use in a bug report.
        right.set_long_mode(lv.label.LONG_MODE.WRAP)
        right.set_flex_grow(1)
        right.set_style_text_align(lv.TEXT_ALIGN.RIGHT, lv.PART.MAIN)


class ScreensSettingsActivity(Activity):
    """List of the user's composed screens: tap to edit, plus Add."""

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)

    def onResume(self, screen):
        super().onResume(screen)
        screen.clean()

        header = lv.label(screen)
        header.set_text("Screens")
        header.set_style_text_font(FontManager.getFont(size=18), lv.PART.MAIN)

        hint = lv.label(screen)
        hint.set_text("Swipe left/right on the dashboard to switch screens.")
        hint.set_style_text_font(FontManager.getFont(size=12), lv.PART.MAIN)
        hint.set_long_mode(lv.label.LONG_MODE.WRAP)
        hint.set_width(lv.pct(100))

        screens = load_screens(self.prefs)
        for index, field_list in enumerate(screens):
            row = lv.obj(screen)
            row.set_width(lv.pct(100))
            row.set_height(lv.SIZE_CONTENT)
            row.set_style_border_width(1, lv.PART.MAIN)
            row.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
            row.add_flag(lv.obj.FLAG.CLICKABLE)
            row.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            row.add_event_cb(lambda e, i=index: self._edit_screen(i), lv.EVENT.CLICKED, None)
            add_focus_border(row)

            title = lv.label(row)
            title.set_text("Screen {}".format(index + 1))
            title.set_style_text_font(FontManager.getFont(size=16), lv.PART.MAIN)
            title.set_pos(0, 0)

            names = ", ".join(FIELD_TITLES.get(f, f) for f in field_list)
            detail = lv.label(row)
            detail.set_text(names if names else "(empty)")
            detail.set_style_text_font(FontManager.getFont(size=12), lv.PART.MAIN)
            detail.set_style_text_opa(lv.OPA._60, lv.PART.MAIN)
            detail.set_long_mode(lv.label.LONG_MODE.DOTS)
            detail.set_width(lv.pct(100))
            detail.set_pos(0, 20)

        # Going back is the floating return button, the same affordance
        # every other settings page here uses. Add takes the row, minus
        # the corner that button floats over — otherwise the end of a
        # full-width Add would sit exactly under it and mis-tap as Back.
        actions = _button_row(screen)
        _row_button(actions, lv.SYMBOL.PLUS + "  Add", self._add_screen, grow=1)
        spacer = lv.obj(actions)
        spacer.set_size(50, 1)
        spacer.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        spacer.set_style_border_width(0, lv.PART.MAIN)
        _add_floating_back(screen, self.finish)

    def _edit_screen(self, index):
        intent = Intent(activity_class=ScreenEditActivity)
        intent.putExtra("prefs", self.prefs)
        intent.putExtra("index", index)
        self.startActivity(intent)

    def _add_screen(self, event=None):
        # Opens the editor in "new screen" mode; the screen is only
        # created if the user taps Save.
        self._edit_screen(len(load_screens(self.prefs)))


class ScreenEditActivity(Activity):
    """Which fields appear on this screen, and in what order.

    One screen: the chosen fields sit at the top in the order they will
    be laid out, draggable by their handles, and the rest are listed
    below by category. Changes are held in memory and only persisted by
    the Save button; backing out without saving discards them."""

    ROW_H = 30

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        self.index = extras.get("index", 0)
        self._selected = []
        self._is_new = False
        self._loaded = False
        self._rows = []
        self._drag_idx = None
        self._drag_target = 0
        self._drag_y0 = 0
        self._row_h = 30
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self._screen = screen
        self.setContentView(screen)

    def onResume(self, screen):
        super().onResume(screen)
        self._screen = screen
        if not self._loaded:
            # Only on the way in: re-reading here would throw away the
            # edits made before switching modes.
            screens = load_screens(self.prefs)
            self._is_new = self.index >= len(screens)
            self._selected = (["block_height"] if self._is_new
                              else list(screens[self.index]))
            self._loaded = True
        self._render()

    def _render(self):
        screen = self._screen
        # Selecting a field rebuilds the list; without this the view would
        # jump back to the top on every tap.
        screen.update_layout()
        keep_scroll = screen.get_scroll_y()
        screen.clean()
        self._drag_idx = None
        self._rows = []

        header = lv.label(screen)
        header.set_text("New screen" if self._is_new
                        else "Screen {} fields".format(self.index + 1))
        header.set_style_text_font(FontManager.getFont(size=18), lv.PART.MAIN)

        self._render_selected(screen)
        for name, field_ids in FIELD_CATEGORIES:
            available = [f for f in field_ids if f not in self._selected]
            if not available:
                continue
            self._section(screen, name)
            for field_id in available:
                self._available_row(screen, field_id)

        actions = _button_row(screen)
        _row_button(actions, lv.SYMBOL.CLOSE + "  Cancel", self.finish, grow=1)
        _row_button(actions, lv.SYMBOL.OK + "  Save", self._save, grow=2)

        if not self._is_new and len(load_screens(self.prefs)) > 1:
            delete_btn = lv.button(screen)
            delete_btn.set_width(lv.pct(100))
            delete_btn.add_event_cb(self._delete_screen, lv.EVENT.CLICKED, None)
            add_focus_border(delete_btn)
            delete_label = lv.label(delete_btn)
            delete_label.set_text(lv.SYMBOL.TRASH + "  Delete Screen")
            delete_label.center()

        screen.update_layout()
        screen.scroll_to_y(keep_scroll, 0)

    def _section(self, screen, text):
        label = lv.label(screen)
        label.set_text(text.upper())
        label.set_style_text_font(FontManager.getFont(size=12), lv.PART.MAIN)
        label.set_style_text_opa(lv.OPA._50, lv.PART.MAIN)
        label.set_style_text_letter_space(1, lv.PART.MAIN)
        label.set_style_pad_top(4, lv.PART.MAIN)

    def _available_row(self, screen, field_id):
        """An unselected field: tap to move it up into Selected."""
        row = lv.obj(screen)
        row.set_width(lv.pct(100))
        row.set_height(self.ROW_H - 3)
        row.set_style_pad_all(0, lv.PART.MAIN)
        row.set_style_border_width(0, lv.PART.MAIN)
        row.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        row.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        row.add_flag(lv.obj.FLAG.CLICKABLE)
        row.add_event_cb(lambda e, f=field_id: self._select(f),
                         lv.EVENT.CLICKED, None)
        add_focus_border(row)
        name = lv.label(row)
        name.set_text(lv.SYMBOL.PLUS + "  " + FIELD_TITLES.get(field_id, field_id))
        name.set_style_text_font(FontManager.getFont(size=14), lv.PART.MAIN)
        name.align(lv.ALIGN.LEFT_MID, 6, 0)

    def _render_selected(self, screen):
        """The chosen fields, in the order they will be laid out.

        Rows live in a fixed-height container with scroll chaining off, so
        sliding one reorders instead of scrolling the page behind it — the
        reason this can share a screen with the full field list at all."""
        self._section(screen, "Selected")
        n = len(self._selected)
        cont = lv.obj(screen)
        cont.set_width(lv.pct(100))
        cont.set_height(self.ROW_H * n)
        cont.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        cont.set_style_border_width(0, lv.PART.MAIN)
        cont.set_style_pad_all(0, lv.PART.MAIN)
        cont.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        cont.remove_flag(lv.obj.FLAG.SCROLLABLE)
        _no_scroll_chain(cont)

        for i, field_id in enumerate(self._selected):
            row = lv.obj(cont)
            row.set_size(lv.pct(100), self.ROW_H - 3)
            row.set_pos(0, i * self.ROW_H)
            row.set_style_border_width(1, lv.PART.MAIN)
            row.set_style_radius(4, lv.PART.MAIN)
            row.set_style_pad_all(0, lv.PART.MAIN)
            row.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            row.add_flag(lv.obj.FLAG.CLICKABLE)
            _no_scroll_chain(row)

            name = lv.label(row)
            name.set_text("{}  {}".format(i + 1,
                                          FIELD_TITLES.get(field_id, field_id)))
            name.set_style_text_font(FontManager.getFont(size=14), lv.PART.MAIN)
            name.align(lv.ALIGN.LEFT_MID, 6, 0)

            # A button, not just an ornament: tapping or ENTERing it moves
            # the field one place down (wrapping at the end), which is the
            # only way to reorder without a touchscreen to drag on. A drag
            # started on the row body still works as before.
            grip = lv.button(row)
            grip.set_size(26, self.ROW_H - 5)
            grip.align(lv.ALIGN.RIGHT_MID, -2, 0)
            grip.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
            grip.set_style_shadow_width(0, lv.PART.MAIN)
            grip.add_event_cb(lambda e, f=field_id: self._nudge(f),
                              lv.EVENT.CLICKED, None)
            add_focus_border(grip)
            grip_icon = lv.label(grip)
            grip_icon.set_text(lv.SYMBOL.LIST)
            grip_icon.set_style_text_font(FontManager.getFont(size=14), lv.PART.MAIN)
            grip_icon.set_style_text_opa(lv.OPA._50, lv.PART.MAIN)
            grip_icon.center()

            # Its own button, so pressing it never starts a drag: LVGL
            # delivers the press to the topmost object under the finger.
            drop = lv.button(row)
            drop.set_size(28, self.ROW_H - 5)
            drop.align(lv.ALIGN.RIGHT_MID, -30, 0)
            drop.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
            drop.set_style_shadow_width(0, lv.PART.MAIN)
            drop.add_event_cb(lambda e, f=field_id: self._deselect(f),
                              lv.EVENT.CLICKED, None)
            add_focus_border(drop)
            cross = lv.label(drop)
            cross.set_text(lv.SYMBOL.CLOSE)
            cross.set_style_text_font(FontManager.getFont(size=12), lv.PART.MAIN)
            cross.center()

            row.add_event_cb(lambda e, i=i: self._drag_start(i),
                             lv.EVENT.PRESSED, None)
            row.add_event_cb(lambda e: self._drag_move(),
                             lv.EVENT.PRESSING, None)
            row.add_event_cb(lambda e: self._drag_end(),
                             lv.EVENT.RELEASED, None)
            # Without this a drag that slips off the row leaves the list
            # stuck mid-reorder.
            row.add_event_cb(lambda e: self._drag_end(),
                             lv.EVENT.PRESS_LOST, None)
            self._rows.append(row)

        if n > 1:
            hint = lv.label(screen)
            hint.set_text("Drag to reorder or tap " + lv.SYMBOL.LIST
                          + " to move down, " + lv.SYMBOL.CLOSE + " to remove.")
            hint.set_style_text_font(FontManager.getFont(size=12), lv.PART.MAIN)
            hint.set_style_text_opa(lv.OPA._50, lv.PART.MAIN)

    def _select(self, field_id):
        if field_id in self._selected:
            return
        if len(self._selected) >= MAX_FIELDS_PER_SCREEN:
            return
        # Appended, not sorted: the order is the user's to set.
        self._selected = list(self._selected) + [field_id]
        self._render()

    def _deselect(self, field_id):
        if len(self._selected) <= 1:
            return                     # a screen must keep at least one field
        self._selected = [f for f in self._selected if f != field_id]
        self._render()

    def _nudge(self, field_id):
        """Move a field one place down, wrapping to the top from the end.

        Repeated presses walk it to any position, so the whole ordering is
        reachable from a keypad without a drag.
        """
        if field_id not in self._selected or len(self._selected) < 2:
            return
        i = self._selected.index(field_id)
        self._selected.pop(i)
        self._selected.insert((i + 1) % (len(self._selected) + 1), field_id)
        self._render()

    def _pointer_y(self):
        indev = lv.indev_active()
        if indev is None:
            return None
        point = lv.point_t()
        indev.get_point(point)
        return point.y

    def _drag_start(self, index):
        y = self._pointer_y()
        if y is None or index >= len(self._rows):
            return
        self._drag_idx = index
        self._drag_target = index
        self._drag_y0 = y
        self._rows[index].move_foreground()

    def _drag_move(self):
        if self._drag_idx is None:
            return
        y = self._pointer_y()
        if y is None:
            return
        offset = y - self._drag_y0
        self._rows[self._drag_idx].set_y(self._drag_idx * self.ROW_H + offset)
        target = self._drag_idx + int(round(offset / float(self.ROW_H)))
        target = max(0, min(len(self._rows) - 1, target))
        if target != self._drag_target:
            self._drag_target = target
            self._open_slot()

    def _open_slot(self):
        """Lay the untouched rows out around an empty slot at the target,
        so the gap shows where the field will land."""
        slot = 0
        for i, row in enumerate(self._rows):
            if i == self._drag_idx:
                continue
            if slot == self._drag_target:
                slot += 1
            row.set_y(slot * self.ROW_H)
            slot += 1

    def _drag_end(self):
        if self._drag_idx is None:
            return
        source, target = self._drag_idx, self._drag_target
        self._drag_idx = None
        if target != source:
            self._selected.insert(target, self._selected.pop(source))
        self._render()          # renumbers and snaps everything back

    def _save(self, event=None):
        screens = load_screens(self.prefs)
        if self._is_new:
            screens.append(list(self._selected))
        elif self.index < len(screens):
            screens[self.index] = list(self._selected)
        save_screens(self.prefs, screens)
        self.finish()

    def _delete_screen(self, event=None):
        screens = load_screens(self.prefs)
        if len(screens) > 1 and self.index < len(screens):
            screens.pop(self.index)
            save_screens(self.prefs, screens)
        self.finish()
