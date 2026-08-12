# market_data.py — fetches block height, prices and fee data from a
# mempool.space-compatible API. Each endpoint is fetched independently so
# one failing endpoint doesn't blank the others.

import json
import time

from mpos import DownloadManager

DEFAULT_BASE_URL = "https://mempool.space"

CURRENCIES = ("USD", "EUR", "GBP", "CAD", "CHF", "AUD", "JPY")

# Chart ranges: label -> (hours covered, max points kept).
#
# The /historical-price feed is one big newest-first list of hourly
# entries (~2.6 years of them before it thins to daily), the server
# ignores Range requests, and reaching further back means streaming more
# of it: roughly 1 KB for a day, 5 KB for a week, 23 KB for a month and
# 276 KB for a year. So one pass fills every range at once — the short
# ranges are just prefixes of the long ones — and the point caps keep
# each series small enough to plot and cache.
# Point caps sized to the display: a full-screen chart is 302 px wide, so
# ~300 points is one per pixel column — more would be invisible. The cap
# costs no bandwidth (the same feed pass is read either way; the cap only
# decides how many parsed entries are kept), so 30d and 1y were raised
# from 180 to 300 for free. 24h and 7d list every hourly sample there is:
# the upstream feed is the limit, not the cap.
RANGE_SPECS = {
    "24h": (24, 24),
    "7d": (168, 168),
    "30d": (720, 300),
    "1y": (8760, 300),
    # One halving cycle. 4 x 8766 h (mean Gregorian year); the ~300-point
    # cap makes the stride 117 h (~4.9 days), i.e. 299 plotted points --
    # the same one-per-pixel ceiling as the other ranges.
    "4y": (35064, 300),
}
# How often each range is worth re-fetching in full, given what it costs
# to reach. The underlying data is hourly, so refetching faster than that
# buys nothing — and the long ranges are kept current between full
# fetches by appending the live spot price (see step_seconds below), so
# they only need an occasional canonical re-read.
# Full re-fetch cadences, sized so each re-read learns ~5 new points (a
# 4y line gains one point per ~117 h, so re-reading its ~2.8 MB more than
# monthly would re-download the lot to learn nothing); between re-reads
# extend_series keeps every range current from the live spot price.
RANGE_REFRESH = {"24h": 1800, "7d": 3600, "30d": 21600, "1y": 604800,
                 "4y": 28 * 86400}
DEFAULT_RANGE = "24h"


# Self-recorded 24h series. The upstream feed is hourly, but the app
# already polls the spot price every minute, so it can build a finer
# picture of the last day for free. 288 slots of 5 minutes is as close
# to the display's ~302-pixel-column ceiling as a whole number of
# minutes gets; finer slots would draw nothing extra.
FINE_SLOT_SECONDS = 300
FINE_SLOTS = 288


# Currencies whose series are kept in reserve after a switch, so hopping
# to another currency and back doesn't throw away a day of recording.
# Bounded because every archived currency costs ~700 floats in the cache.
ARCHIVE_LIMIT = 3


def _snapshot(state):
    """Everything that is specific to the currency in use."""
    return {
        "price": state.get("price"),
        "fine": list(state.get("fine") or []),
        "fine_ts": state.get("fine_ts"),
        "charts": {k: list(v) for k, v in (state.get("charts") or {}).items()},
        "charts_ts": dict(state.get("charts_ts") or {}),
        "fetched_at": dict(state.get("fetched_at") or {}),
    }


def _restore_snapshot(state, snap, currency):
    state["price"] = snap.get("price")
    state["fine"] = list(snap.get("fine") or [])
    state["fine_ts"] = snap.get("fine_ts")
    state["charts"] = {k: list(v) for k, v in (snap.get("charts") or {}).items()}
    state["charts_ts"] = dict(snap.get("charts_ts") or {})
    state["fetched_at"] = dict(snap.get("fetched_at") or {})
    state["fine_currency"] = currency
    state["charts_currency"] = currency


def switch_currency(state, currency):
    """Move the working set to `currency`.

    The outgoing currency's series are parked in an archive rather than
    discarded, and the incoming currency's are restored if we still have
    them — so switching USD -> EUR -> USD keeps the recording that was
    already built up. Restored series carry their original timestamps, so
    anything that went stale while parked is simply due for a refresh and
    gets re-read on the normal schedule."""
    if state.get("series_currency") == currency:
        return False

    archive = state.setdefault("archive", {})
    order = state.setdefault("archive_order", [])
    previous = state.get("series_currency")
    if previous:
        archive[previous] = _snapshot(state)
        if previous in order:
            order.remove(previous)
        order.append(previous)
        while len(order) > ARCHIVE_LIMIT:
            archive.pop(order.pop(0), None)

    snap = archive.pop(currency, None)
    if currency in order:
        order.remove(currency)
    if snap:
        _restore_snapshot(state, snap, currency)
    else:
        state["price"] = None
        state["fine"] = []
        state["fine_ts"] = None
        state["charts"] = {}
        state["charts_ts"] = {}
        state["fetched_at"] = {}
        state["fine_currency"] = currency
        state["charts_currency"] = currency
    state["series_currency"] = currency
    return True


def resample_fine(series, old_slot, new_slot):
    """Re-express a recording made at old_slot seconds per sample in
    new_slot seconds per sample, covering the same span of time.

    Real samples land exactly on their new positions (600 -> 300 puts
    every old sample on an even index); positions between them are the
    straight line the chart drew between those samples anyway. Used once,
    when a cached recording predates a slot-size change — the alternative
    is discarding a day of real data."""
    if not series or old_slot == new_slot or len(series) == 1:
        return list(series)
    n = len(series)
    out_n = ((n - 1) * old_slot) // new_slot + 1
    out = []
    for k in range(out_n):
        pos = k * new_slot / old_slot
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        out.append(series[lo] + (series[hi] - series[lo]) * (pos - lo))
    return out


def hourly_price_at(state, when):
    """Price from the hourly feed at `when`, or None if the feed we hold
    doesn't cover that moment. Used to fill recording gaps with real data
    instead of a straight line."""
    hourly = (state.get("charts") or {}).get("24h") or []
    newest = (state.get("charts_ts") or {}).get("24h")
    if len(hourly) < 2 or not newest:
        return None
    oldest = newest - (len(hourly) - 1) * 3600
    if when < oldest or when > newest:
        return None
    pos = (when - oldest) / 3600.0
    lo = int(pos)
    hi = min(lo + 1, len(hourly) - 1)
    return hourly[lo] + (hourly[hi] - hourly[lo]) * (pos - lo)


def record_fine(state, max_age=None):
    """Fold the freshly polled spot price into the 10-minute series.

    One value per slot (the latest sample wins). Slots missed while the
    app wasn't running are filled from the hourly feed — real prices —
    and only fall back to a straight line for moments the feed doesn't
    reach. A gap longer than the whole window starts the series over.

    A price older than max_age is not a new observation, so it is not
    recorded: re-recording the last-known price once a slot turns an
    outage into a flat line that looks exactly like a market that did
    not move, and caches it. Skipping leaves the slots missing, which
    the gap fill above then covers from the hourly feed — real prices
    for the time the feed was down."""
    price = state.get("price")
    if price is None:
        return False
    if max_age is None:
        max_age = FINE_SLOT_SECONDS
    price_ts = (state.get("updated_at") or {}).get("price")
    if price_ts is not None and time.time() - price_ts > max_age:
        return False
    currency = state.get("currency", "USD")
    if state.get("fine_currency") != currency:
        state["fine"] = []
        state["fine_ts"] = None
        state["fine_currency"] = currency

    now = time.time()
    slot = int(now // FINE_SLOT_SECONDS) * FINE_SLOT_SECONDS
    series = state.get("fine") or []
    last = state.get("fine_ts")

    if not series or last is None:
        state["fine"] = [float(price)]
        state["fine_ts"] = slot
        return True
    if slot == last:
        series[-1] = float(price)
        state["fine"] = series
        return True
    if slot < last:
        return False                      # clock stepped backwards

    missing = int((slot - last) // FINE_SLOT_SECONDS)
    if missing >= FINE_SLOTS:             # nothing of the old window survives
        state["fine"] = [float(price)]
        state["fine_ts"] = slot
        return True
    prev = series[-1]
    for k in range(1, missing):
        when = last + k * FINE_SLOT_SECONDS
        real = hourly_price_at(state, when)
        series.append(float(real) if real is not None
                      else prev + (float(price) - prev) * k / missing)
    series.append(float(price))
    while len(series) > FINE_SLOTS:
        series.pop(0)
    state["fine"] = series
    state["fine_ts"] = slot
    return True


def fine_series(state):
    """The 24h series to plot: self-recorded 10-minute samples where they
    exist, with the older remainder of the window filled in from the
    hourly feed, so the chart always spans a full 24 hours."""
    fine = state.get("fine") or []
    if state.get("fine_currency") != state.get("currency", "USD"):
        fine = []
    if len(fine) >= FINE_SLOTS:
        return fine[-FINE_SLOTS:]

    hourly = (state.get("charts") or {}).get("24h") or []
    if len(hourly) < 2:
        return fine if len(fine) >= 2 else []
    if len(fine) < 2:
        return hourly

    # Back-fill the older part of the window from the hourly feed:
    # straight lines between the real hourly samples, nothing invented.
    need = FINE_SLOTS - len(fine)
    span = len(hourly) - 1
    out = []
    for i in range(need):
        pos = i * span / (FINE_SLOTS - 1)
        lo = int(pos)
        hi = min(lo + 1, span)
        out.append(hourly[lo] + (hourly[hi] - hourly[lo]) * (pos - lo))
    return out + fine


def step_seconds(label):
    """How much real time one plotted point of a range represents — i.e.
    how often that series wants a fresh sample. ~1 h for 24h/7d, ~4 h for
    30d, ~49 h for 1y."""
    hours, cap = RANGE_SPECS.get(label, RANGE_SPECS[DEFAULT_RANGE])
    return max(1, hours // cap) * 3600

# Hard ceiling per fetch, derived from the horizon: entries run ~40 bytes
# in USD and ~58 in currencies that carry both local and USD prices, so
# 80 bytes/hour leaves slack without letting a malformed feed run away.
_BYTES_PER_HOUR = 80
# How much must arrive before the loading indicator is updated again.
# 4 KB is a few updates for a 24h fetch and a smooth count for a 4y one.
_PROGRESS_EVERY = 4096
_BYTES_FLOOR = 8192


class _EnoughHistory(Exception):
    """Sentinel used to stop the streaming download early (not an error)."""


class _HistoryCollector:
    """Incremental newest-first parser for the price feed.

    Entries are parsed as chunks arrive and immediately thinned into
    per-range buckets, so a year's worth (~276 KB on the wire) never
    exists in memory at once — only a small carry buffer and ~180 points
    per range. Entries look like {"time":1785956400,"USD":64741}, or
    {"time":...,"EUR":55954,"USD":64736} for non-USD currencies, so the
    currency key is looked up inside each object."""

    def __init__(self, currency, labels):
        self.key = '"%s":' % currency
        self.labels = [lb for lb in labels if lb in RANGE_SPECS]
        if not self.labels:
            self.labels = [DEFAULT_RANGE]
        self.buckets = {lb: [] for lb in self.labels}
        # Thin by TIMESTAMP, not by entry count: the feed is hourly for
        # only ~2.6 years and daily beyond that, so "every Nth entry"
        # would stretch a long range's tail (117 daily entries span 117
        # days, not 117 hours) and warp the time axis. One kept point per
        # stride-wide time bucket keeps the axis uniform whatever the
        # feed's cadence underneath.
        self.stride_h = {}
        self.last_bucket = {}
        for lb in self.labels:
            hours, cap = RANGE_SPECS[lb]
            self.stride_h[lb] = max(1, (hours + cap - 1) // cap)
            self.last_bucket[lb] = -1
        self.max_hours = max(RANGE_SPECS[lb][0] for lb in self.labels)
        self.newest = None
        self.carry = ""
        self.done = False

    def feed(self, text):
        """Consume a chunk; returns True once the horizon is reached."""
        self.carry += text
        while True:
            start = self.carry.find('{"time":')
            if start < 0:
                self.carry = ""
                break
            end = self.carry.find('}', start)
            if end < 0:                      # entry split across chunks
                self.carry = self.carry[start:]
                break
            entry = self.carry[start:end]
            self.carry = self.carry[end + 1:]
            self._take(entry)
            if self.done:
                return True
        return self.done

    def _take(self, entry):
        try:
            t = int(entry[8:entry.find(',', 8)])      # after '{"time":'
            kpos = entry.find(self.key)
            if kpos < 0:
                return
            vstart = kpos + len(self.key)
            vend = entry.find(',', vstart)
            if vend < 0:
                vend = len(entry)
            price = float(entry[vstart:vend])
        except (ValueError, IndexError):
            return
        if self.newest is None:
            self.newest = t
        age_h = max(0, self.newest - t) // 3600
        if age_h >= self.max_hours:
            self.done = True
            return
        # Newest-first feed: the first entry seen in a bucket is the
        # newest inside it, which is the one to keep.
        for lb in self.labels:
            hours, _cap = RANGE_SPECS[lb]
            if age_h >= hours:
                continue
            bucket = age_h // self.stride_h[lb]
            if bucket != self.last_bucket[lb]:
                self.last_bucket[lb] = bucket
                self.buckets[lb].append(price)

    def series(self):
        """label -> prices, oldest-first, ready to plot."""
        out = {}
        for lb, prices in self.buckets.items():
            if len(prices) >= 2:
                out[lb] = list(reversed(prices))
        return out


class MarketData:
    """One-shot async fetcher. Results land in the dict passed to fetch()."""

    def __init__(self, base_url=None):
        base = (base_url or DEFAULT_BASE_URL).strip()
        if base.endswith("/"):
            base = base[:-1]
        self.base_url = base
        self.last_error = None

    async def _get_json(self, path):
        data = await DownloadManager.download_url(self.base_url + path)
        return json.loads(data)

    async def fetch(self, state, currency="USD", want_priority_fees=False):
        """Update state in place. Returns True if anything was updated.
        Each successful endpoint stamps state["updated_at"][source] so the
        UI can flag stale fields."""
        updated = False
        self.last_error = None
        stamps = state.setdefault("updated_at", {})

        try:
            data = await DownloadManager.download_url(self.base_url + "/api/blocks/tip/height")
            state["height"] = int(data)
            stamps["height"] = time.time()
            updated = True
        except Exception as e:
            self.last_error = e
            print("BlockTV: height fetch failed: {}".format(e))

        try:
            prices = await self._get_json("/api/v1/prices")
            price = prices.get(currency)
            if price:
                state["price"] = float(price)
                stamps["price"] = time.time()
                updated = True
        except Exception as e:
            self.last_error = e
            print("BlockTV: price fetch failed: {}".format(e))

        try:
            blocks = await self._get_json("/api/v1/fees/mempool-blocks")
            if blocks and isinstance(blocks, list):
                median = blocks[0].get("medianFee")
                if median is not None:
                    state["fee"] = float(median)
                    stamps["fees"] = time.time()
                    updated = True
        except Exception as e:
            self.last_error = e
            print("BlockTV: fee fetch failed: {}".format(e))

        # A second, tiny endpoint (~100 bytes) and only when a low/high
        # priority field is actually on a screen — the median comes from
        # mempool-blocks and cannot be read off this one.
        if want_priority_fees:
            try:
                rec = await self._get_json("/api/v1/fees/recommended")
                if isinstance(rec, dict):
                    high, low = rec.get("fastestFee"), rec.get("hourFee")
                    if high is not None:
                        state["fee_high"] = float(high)
                    if low is not None:
                        state["fee_low"] = float(low)
                    if high is not None or low is not None:
                        stamps["fees"] = time.time()
                        updated = True
            except Exception as e:
                self.last_error = e
                print("BlockTV: priority fee fetch failed: {}".format(e))

        return updated

    async def fetch_history(self, state, currency="USD", labels=None,
                            progress=None):
        """Fill state["charts"][label] for each requested chart range.

        Streams the newest-first feed, parsing and thinning as it goes,
        and stops as soon as the longest requested range is covered — so
        a 24h chart costs ~1 KB and a year ~276 KB, with memory flat
        either way. Ranges shorter than the one being fetched are filled
        from the same pass for free.

        progress(bytes_so_far) is called as the stream arrives, throttled
        so a slow display update can never become the bottleneck for the
        download it is reporting on."""
        collector = _HistoryCollector(currency, labels or [DEFAULT_RANGE])
        ceiling = max(_BYTES_FLOOR, collector.max_hours * _BYTES_PER_HOUR)
        size = 0
        reported = 0

        async def on_chunk(chunk):
            nonlocal size, reported
            size += len(chunk)
            if progress is not None and size - reported >= _PROGRESS_EVERY:
                reported = size
                try:
                    progress(size)
                except Exception:
                    pass          # a failing indicator must not kill the fetch
            try:
                text = chunk.decode("utf-8")
            except Exception:
                # A multi-byte character split across chunks would only
                # appear in prose fields; prices are ASCII, so skipping a
                # malformed chunk is safer than aborting the whole fetch.
                return
            if collector.feed(text) or size >= ceiling:
                raise _EnoughHistory()

        url = "{}/api/v1/historical-price?currency={}".format(self.base_url, currency)
        try:
            await DownloadManager.download_url(url, chunk_callback=on_chunk)
        except _EnoughHistory:
            pass                      # expected: we stopped the stream ourselves
        except Exception as e:
            self.last_error = e
            print("BlockTV: history fetch failed: {}".format(e))

        series = collector.series()
        if not series:
            print("BlockTV: history produced no usable series")
            return False

        charts = state.get("charts")
        if not isinstance(charts, dict) or state.get("charts_currency") != currency:
            charts = {}
            state["charts_ts"] = {}
        charts.update(series)
        state["charts"] = charts
        state["charts_currency"] = currency
        now = time.time()
        stamps = state.setdefault("charts_ts", {})
        for label in series:
            stamps[label] = now
        state.setdefault("updated_at", {})["history"] = now
        return True


def extend_series(state):
    """Advance the long ranges using the spot price we already poll.

    A 1y line gains one plotted point every ~49 hours and a 30d line one
    every ~4 — so between full re-reads they can be kept current for free
    by appending the live price when a step has elapsed, instead of
    re-downloading a quarter of a megabyte to learn one number.

    Returns True if any series changed."""
    price = state.get("price")
    charts = state.get("charts")
    if price is None or not charts:
        return False
    stamps = state.setdefault("charts_ts", {})
    now = time.time()
    changed = False
    for label, series in charts.items():
        cap = RANGE_SPECS.get(label, (0, 0))[1]
        last = stamps.get(label)
        if not cap or not series or not last:
            continue
        if now - last < step_seconds(label):
            continue
        series.append(float(price))
        while len(series) > cap:
            series.pop(0)
        stamps[label] = now
        changed = True
    return changed
