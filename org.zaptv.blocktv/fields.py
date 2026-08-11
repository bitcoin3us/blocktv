# fields.py — BlockTV field registry, derived metrics and formatting.
#
# Every displayable data field has an id, a title, and a render function
# that maps the shared `state` dict to (value_text, sub_text). Keeping
# this separate from the UI lets screens be rebuilt from a plain list of
# field ids (the user's custom layouts).

from mpos import NumberFormat

HALVING_INTERVAL = 210000
SATS_PER_BTC = 100_000_000

# Canonical field order — also the order shown in the layout editor.
FIELD_IDS = [
    "block_height",
    "spot_price",
    "price_chart",
    "price_chart_7d",
    "price_chart_30d",
    "price_chart_1y",
    "price_chart_4y",
    "moscow_time",
    "halving",
    "fee_rate",
    "fee_low",
    "fee_high",
    "supply",
    "market_cap",
    "clock",
    "zap",
    "nwc_balance",
]

# Fields drawn as a graph rather than a number. The UI builds a chart
# widget for these; render_field still supplies the caption text.
CHART_FIELDS = ("price_chart", "price_chart_7d",
                "price_chart_30d", "price_chart_1y", "price_chart_4y")

# How many trailing hourly points each chart field plots, and how its
# range is labelled.
CHART_LABELS = {
    "price_chart": "24h",
    "price_chart_7d": "7d",
    "price_chart_30d": "30d",
    "price_chart_1y": "1y",
    "price_chart_4y": "4y",
}


def chart_series(field_id, state):
    """The price series a chart field plots, oldest-first.

    The 24h chart prefers the app's own 10-minute recording (six times
    the detail of the hourly feed) and falls back to the feed until that
    has filled in."""
    label = CHART_LABELS.get(field_id, "24h")
    if label == "24h":
        from market_data import fine_series
        fine = fine_series(state)
        if len(fine) >= 2:
            return fine
    charts = state.get("charts") or {}
    return charts.get(label) or []

FIELD_TITLES = {
    "block_height": "Block Height",
    "spot_price": "Price",
    "price_chart": "24h Chart",
    "price_chart_7d": "7d Chart",
    "price_chart_30d": "30d Chart",
    "price_chart_1y": "1y Chart",
    "price_chart_4y": "4y Chart",
    "moscow_time": "Moscow Time",
    "halving": "Halving",
    "fee_rate": "Median Fee",
    "fee_low": "Low Priority Fee",
    "fee_high": "High Priority Fee",
    "supply": "Supply",
    "market_cap": "Market Cap",
    "clock": "Clock",
    "zap": "Latest Zap",
    "nwc_balance": "Wallet",
}

# Grouped for the field picker. Every id in FIELD_IDS must appear exactly
# once; the check below fails the import rather than quietly hiding a field
# from the picker if one is ever added to FIELD_IDS and not to a group.
FIELD_CATEGORIES = (
    ("Price", ("spot_price", "price_chart", "price_chart_7d",
               "price_chart_30d", "price_chart_1y", "price_chart_4y", "market_cap")),
    # Grouped by what they read like, not where the number comes from:
    # moscow time is price-derived but shown as a clock, and that is how
    # someone scanning this list will look for it.
    ("Time", ("clock", "moscow_time", "halving")),
    ("Chain", ("block_height", "supply")),
    ("Fees", ("fee_rate", "fee_high", "fee_low")),
    ("Wallet", ("zap", "nwc_balance")),
)

_grouped = [f for _name, ids in FIELD_CATEGORIES for f in ids]
assert sorted(_grouped) == sorted(FIELD_IDS), "FIELD_CATEGORIES must cover FIELD_IDS exactly"
del _grouped

# field id -> (state key, unit caption)
_FEE_FIELDS = {
    "fee_rate": ("fee", "sat/vB"),
    "fee_low": ("fee_low", "sat/vB · ~1 hr"),
    "fee_high": ("fee_high", "sat/vB · next block"),
}

PLACEHOLDER = "--"

# Which data source(s) feed each field — used for stale-data warnings.
# clock is local-only and never goes stale.
FIELD_SOURCES = {
    "block_height": ("height",),
    "spot_price": ("price",),
    "price_chart": ("history",),
    "price_chart_7d": ("history",),
    "price_chart_30d": ("history",),
    "price_chart_1y": ("history",),
    "price_chart_4y": ("history",),
    "moscow_time": ("price",),
    "halving": ("height",),
    "fee_rate": ("fees",),
    "fee_low": ("fees",),
    "fee_high": ("fees",),
    "supply": ("height",),
    "market_cap": ("height", "price"),
    "clock": (),
    "zap": ("nostr",),
    "nwc_balance": ("nwc",),
}


def fmt_int(n):
    return NumberFormat.format_number(int(n))


def supply_sats(height):
    """Total coin issuance after `height` blocks (standard approximation:
    genesis + unclaimed subsidies ignored)."""
    sats = 0
    subsidy = 50 * SATS_PER_BTC
    blocks = height
    while blocks > 0 and subsidy > 0:
        take = min(blocks, HALVING_INTERVAL)
        sats += take * subsidy
        blocks -= take
        subsidy //= 2
    return sats


def moscow_time_str(price):
    """Sats per fiat unit rendered clock-style: 847 -> 8:47"""
    sats = round(SATS_PER_BTC / price)
    s = str(sats)
    if len(s) < 3:
        s = "0" * (3 - len(s)) + s
    return s[:-2] + ":" + s[-2:], sats

def big_number_str(value):
    """2.36T / 415.7B / 900.1M style compaction."""
    for div, suffix in ((1_000_000_000_000, "T"), (1_000_000_000, "B"), (1_000_000, "M")):
        if value >= div:
            v = value / div
            return ("%.2f%s" if v < 100 else "%.1f%s") % (v, suffix)
    return fmt_int(value)


def duration_str(blocks):
    """Rough human duration for a block count at 10 min/block."""
    minutes = blocks * 10
    days = minutes // (60 * 24)
    if days >= 365:
        years = days // 365
        rem = days % 365
        return "~%dy %dd" % (years, rem)
    if days >= 1:
        hours = (minutes // 60) % 24
        return "~%dd %dh" % (days, hours)
    return "~%dh %dm" % (minutes // 60, minutes % 60)


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def render_field(field_id, state):
    """Return (value_text, sub_text) for a field given the shared state dict.

    state keys: height, price (float, selected currency), currency,
    fee (float sat/vB), zap (dict or None), balance (int sats or None),
    localtime (time tuple or None).
    """
    height = state.get("height")
    price = state.get("price")
    currency = state.get("currency", "USD")

    if field_id == "block_height":
        if height is None:
            return PLACEHOLDER, "blocks"
        return fmt_int(height), "blocks"

    if field_id == "spot_price":
        if price is None:
            return PLACEHOLDER, currency
        return fmt_int(round(price)), currency

    if field_id in CHART_FIELDS:
        label = CHART_LABELS.get(field_id, "24h")
        series = chart_series(field_id, state)
        if len(series) < 2:
            return PLACEHOLDER, label
        first, last = series[0], series[-1]
        pct = ((last - first) / first * 100) if first else 0
        sign = "+" if pct >= 0 else ""
        return "{}{:.1f}%".format(sign, pct), "{} {}".format(label, currency)

    if field_id == "moscow_time":
        if not price:
            return PLACEHOLDER, "sat/" + currency
        text, sats = moscow_time_str(price)
        return text, "%s sat/%s" % (fmt_int(sats), currency)

    if field_id == "halving":
        if height is None:
            return PLACEHOLDER, "blocks left"
        next_halving = ((height // HALVING_INTERVAL) + 1) * HALVING_INTERVAL
        remaining = next_halving - height
        return fmt_int(remaining), "blocks · " + duration_str(remaining)

    if field_id in _FEE_FIELDS:
        # mempool.space's own wording: high priority is its next-block
        # estimate, low priority its within-the-hour one.
        key, sub = _FEE_FIELDS[field_id]
        fee = state.get(key)
        if fee is None:
            return PLACEHOLDER, sub
        if fee < 10:
            return "%.1f" % fee, sub
        return fmt_int(round(fee)), sub

    if field_id == "supply":
        if height is None:
            return PLACEHOLDER, "BTC"
        btc = supply_sats(height) / SATS_PER_BTC
        return big_number_str(btc), "BTC"

    if field_id == "market_cap":
        if height is None or price is None:
            return PLACEHOLDER, currency
        cap = (supply_sats(height) / SATS_PER_BTC) * price
        return big_number_str(cap), currency

    if field_id == "clock":
        lt = state.get("localtime")
        if not lt:
            return PLACEHOLDER, ""
        value = "%02d:%02d" % (lt[3], lt[4])
        try:
            sub = "%s %d %s" % (WEEKDAYS[lt[6]], lt[2], MONTHS[lt[1] - 1])
        except (IndexError, TypeError):
            sub = ""
        return value, sub

    if field_id == "zap":
        zap = state.get("zap")
        if not zap:
            return PLACEHOLDER, "no zaps yet"
        sats = zap.get("sats")
        value = fmt_int(sats) if sats is not None else "?"
        comment = zap.get("comment") or ""
        sub = comment if comment else "sats"
        return value, sub

    if field_id == "nwc_balance":
        balance = state.get("balance")
        if balance is None:
            return PLACEHOLDER, "sats"
        return fmt_int(balance), "sats"

    return "?", field_id
