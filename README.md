# BlockTV

BlockTV — a clean, customisable Bitcoin dashboard app for
[MicroPythonOS](https://micropythonos.com). The installable app lives in
[`org.zaptv.blocktv/`](org.zaptv.blocktv/).

![BlockTV](docs/screenshot.png)

## Data fields

| Field | Shows | Source |
|---|---|---|
| Block Height | Current chain tip | mempool.space API |
| Price | Spot price in your currency | mempool.space API |
| Moscow Time | Sats per fiat unit, clock-style (`15:67`) | derived |
| Halving | Blocks + estimated time to next halving | derived |
| Median Fee | Median fee rate of the next block (sat/vB) | mempool.space API |
| High Priority Fee | Next-block estimate (sat/vB) | mempool.space API |
| Low Priority Fee | Within-the-hour estimate (sat/vB) | mempool.space API |
| Supply | Circulating supply (issuance schedule) | derived |
| Market Cap | Supply × price | derived |
| Clock | Normal time + date (uses the OS timezone) | on-device |
| Latest Zap | Most recent nostr zap to your npub | nostr relays (kind 9735) |
| Wallet | Wallet balance in sats | Nostr Wallet Connect |
| 24h Chart | Price line graph over the last 24 hours | mempool.space API |
| 7d Chart | Price line graph over the last 7 days | mempool.space API |
| 30d Chart | Price line graph over the last 30 days | mempool.space API |
| 1y Chart | Price line graph over the last year | mempool.space API |
| 4y Chart | Price line graph over one halving cycle | mempool.space API |

All five charts share one fetch. The hourly `historical-price` feed is
newest-first, so reaching further back just means reading more of it —
roughly 1 KB for a day, 5 KB for a week, 23 KB for a month and 276 KB
for a year — and a single pass fills every range in use at once. It is
parsed as it streams and thinned on the fly, so memory stays flat
regardless of range, and each range refreshes on a cadence matched to
its cost — and, crucially, to what it can actually learn: a 1y line
gains one plotted point every ~49 hours, so re-reading a quarter of a
megabyte more often than that tells it nothing. Between full re-reads
the long ranges are advanced for free by appending the spot price the
app already polls every minute. Full re-reads: 30 min (24h), 1 h (7d),
6 h (30d), 7 days (1y), 28 days (4y) — sized so each re-read learns
about five new points. The feed is hourly for roughly 2.6 years and
daily beyond, so entries are thinned into fixed-width time buckets
rather than counted — a 4y chart keeps one point per ~117 hours and its
axis stays uniform across the cadence change, at ~1 MB per monthly
fetch.

The 24h chart goes further: the upstream feed is only hourly, so BlockTV
also records the spot price it already polls into a rolling 24-hour
series at 5-minute resolution (288 slots) — twelve times the feed's
detail, at no extra network cost, and within a few pixels of the
display's own ceiling: a full-screen chart is 302 px wide, so ~300
points is one per pixel column and anything finer would draw nothing.
The long ranges are held to the same standard for free — the 30d and 1y
caps were raised from 180 to one-point-per-pixel territory (240 and 292,
the closest whole-hour strides) without downloading a byte more, since
the cap only decides how many entries of the same feed pass are kept.
A recording made before a slot-size change is re-expressed in the new
slots on load rather than thrown away. It fills in as the device runs, is cached across
restarts, and the not-yet-recorded part of the window is back-filled
from the hourly feed (straight lines between real samples) so the chart
always spans a full day. Slots missed while the app wasn't
running are filled from the hourly feed — real prices — and only fall
back to a straight line for moments that feed doesn't reach; a gap
longer than the whole window starts the recording over. A price that
has stopped arriving is not recorded at all: re-recording the
last-known value once a slot would draw an outage as a flat line
indistinguishable from a market that did not move, and cache it. The
slots are left missing instead, and the same hourly back-fill covers
them with what the price actually did.

Switching currency parks the outgoing currency's series instead of
discarding them, so hopping to another currency and back keeps whatever
recording had already built up. The last three currencies are kept in
reserve, and parked series carry their original timestamps — so anything
that went stale while parked is simply due for a refresh.

Chart series handles are re-read from the widget on every update rather
than cached: `lv_chart_set_point_count()` reallocates the sample array,
and a handle held across that call silently writes where nothing is
drawn.

There is no data smoothing anywhere in the pipeline: every plotted point
is a real sample, thinned only past one-per-pixel, and the only
interpolation left is the straight line between two real hourly samples
while a cold 24h recording is still filling in.

A chart given a screen to itself also shows the current spot price in
its currency, bold and full-strength in the top-right corner — the one
thing the line cannot tell you. Charts sharing a screen with other
fields leave it off, since the space is already spoken for.

Lines are green when the price ends the range higher than it started
and red when lower, judged per range — so a red day inside a green week
shows exactly that. The move over the range is captioned in bold
in the same colour, in the corner of the tile. A late chart uses the
warning colour instead of green or red, so it cannot pass itself off as
a confident reading.
An ordinary move is bold, trend-coloured text. A move big enough to be
worth noticing — past 2% on the day, 5% on the week, 10% on the month,
40% on the year — turns that same label into the filled pill and steps
it up a size, so the badge means something when you see it. The
thresholds are scaled to what is ordinary for each window, so a 3% day
shouts and a 3% year does not. The taller size is reserved in the layout
either way, so crossing a threshold never resizes the chart underneath. Longer series are thinned to the tile's pixel width
so the curve stays readable.

The two priority fees come from a second, tiny endpoint
(`/api/v1/fees/recommended`, ~100 bytes) which is only requested when one
of them is actually on a screen — the median comes from the mempool-blocks
call and cannot be read off it. If that endpoint fails, every other
reading still updates.

Derived fields need no extra requests — height and price are enough to
compute moscow time, halving, supply and market cap on-device.

## Custom screens

Settings → **Screens**. Each screen holds 1–8 fields; the layout
adapts automatically (one huge tile, stacked rows, or a grid) and each
value is scaled to fill its tile — a single field fills the whole screen.
Field changes in the editor apply when you tap **Save**; backing out
discards them.

The editor is one screen. Chosen fields sit at the top under
**Selected**, numbered in the order they will be laid out — left to
right, then down. The rest are listed below by category (Price, Time,
Chain, Fees, Wallet); tapping one moves it up into Selected, and the
✕ on a selected row puts it back. Press a row's handle and slide to
reorder: the others move aside to show where it will land, and the
page behind does not scroll while you drag. Grab a row anywhere except
the leftmost 24 px — MPOS reserves that strip for its back gesture, so
a press starting there never reaches the app. Add as many screens as you like and swipe left/right on
the dashboard to move between them, or press the board's BOOT button to
step through. Page dots at the bottom show where you are.

Pages are built once and then cached, so turning a page is a visibility
switch rather than a rebuild — on the ESP32 that took a swipe from
roughly 0.7–1.9 s down to a few milliseconds. The swipe also acts as
soon as your finger has travelled far enough, instead of waiting for you
to lift it.

## Without a touchscreen

MicroPythonOS runs on boards that have buttons or a joystick instead of a
touchscreen (odroid_go, m5stack_fire, lilygo_t4 and friends), where the
board driver moves focus with the arrows and activates with ENTER. Every
interactive part of BlockTV is registered in the focus group, so all of
it is reachable that way: the settings cog, the screen list, the field
picker, the buttons and the refresh slider. Left/right also turn
dashboard pages, and any key skips the start screen. Reordering fields
needs no drag either — tapping or ENTERing a field's handle moves it one
place down, wrapping at the end, so repeated presses reach any position.
On boards that have one, the BOOT button still steps through screens.

Focus highlights stay invisible until you actually navigate by
direction, so a touch-only device never shows a stray outline.

## Design

The app opens on a BlockTV logo start screen (2 seconds, tap to skip).
Two renderings of the logo ship in `res/drawable-mdpi/`: the artwork as
drawn (`blocktv_logo_light.png`) for light backgrounds, and a dark-mode
copy (`blocktv_logo_dark.png`) where the TV keeps its original colours
but gains a white outline traced around its silhouette, while only the
wordmark has its black ink inverted to white. The gold cube is
untouched in both. The splash picks by background luminance and paints your theme
colour behind it.

Clean and minimal, white-on-black by default. Settings → **Text Color**
and **Background Color** are picked independently (White, Black, Bitcoin
orange, Nostr purple each), so any combination works — orange on black,
black on orange, purple on white, and so on. Picking the same color for
both auto-falls-back to readable text.
Numbers roll odometer-style when they change, and each field's title
turns orange when its data source is 10 minutes late and red at 24
hours (warning hues adapt per theme so they stay visible). "Late" is
measured against what each source actually promises, not a single
clock: a 1y chart refreshes weekly and a 24h chart every 30 minutes, so
each is judged against its own cadence and its own timestamp. When a zap
arrives, the whole display inverts for a few seconds with the amount
and comment (Settings → **Zap Splash** to disable). Settings →
**Screen Flash** can additionally flash the display briefly when a new
block is found, or on any value change, to grab attention. Settings →
**Corner Clock** puts a small local time and date in the bottom-left
corner, sharing the bottom edge with the page dots and the settings cog
but kept out of the page layout, so it stays put as you swipe.

Settings → **About** shows the logo, the board it is running on, the
MicroPythonOS and BlockTV versions, and www.ZapTV.org — the three
numbers worth quoting in a bug report, in one place.

Data is kept in memory (switching screens is instant, no refetch) and
the last-known values persist across restarts — on relaunch they show
immediately with honest stale coloring instead of blank placeholders.

## Setup

- **Currency** — USD, EUR, GBP, CAD, CHF, AUD or JPY (price, moscow time
  and market cap all follow it).
- **Nostr Zaps** — set your npub (or hex pubkey); zap receipts are watched
  on the configured relays. No private key is needed or asked for. The
  default relays (Primal, nos.lol, Snort, offchain.pub) were each checked
  to actually serve kind-9735 zap receipts.
- **Nostr Wallet Connect** — paste a `nostr+walletconnect://` URL to show
  the wallet balance. Incoming NWC payments also trigger the zap splash,
  so zaps to your lightning address show up even without an npub.
- **API Server** — defaults to `https://mempool.space`; point it at a
  self-hosted mempool instance for privacy.

## Install

### On a device (via USB)

```bash
mpremote connect /dev/cu.usbmodem101 fs cp -r org.zaptv.blocktv :/apps/
mpremote connect /dev/cu.usbmodem101 exec "import machine; machine.reset()"
```

(Verify the tree afterwards — `mpremote cp -r` with trailing slashes can
nest directories. See `~/MicroPythonOS/MPOS_APP_DEV.md` §7.)

### Desktop emulator

The app is symlinked into the MicroPythonOS workspace at
`~/MicroPythonOS/internal_filesystem/apps/org.zaptv.blocktv`, so it shows
up in the launcher of the desktop build (`scripts/run_desktop.sh`).

### Regenerating the artwork

The icon and splash logos are built from the authored SVGs in
`~/Documents/Projects/BlockTV` — vector is the source of truth:

```bash
python3 tools/build_assets.py          # --check for a dry run
```

The output is sized to match the sibling ZapTV app — 256 px splash logo
(80% of the screen) and an icon mark filling the tile width — so the two
apps look like one family. Needs `rsvg-convert` (`brew install librsvg`). macOS `qlmanage` can also
render SVG but flattens the alpha channel, so it cannot be used here. The
script is idempotent and reports what, if anything, changed.

### Package as .mpk

```bash
cd org.zaptv.blocktv
find . -type f | sort | TZ=CET zip -X -r0 ../org.zaptv.blocktv_0.2.0.mpk -@
```

## Notes

- `assets/nostr_service.py` is the shared NostrManager used by the nostr
  and Lightning Piggy apps (plus one additive `configure_relays()` method
  for identity-free zap watching). The manager is a device-wide singleton:
  whichever app's copy imports first wins, and NWC callbacks are global —
  running BlockTV's NWC integration simultaneously with another NWC app
  means the last one to resume gets the callbacks.
- All network fetches degrade gracefully: fields show `--` until data
  arrives, and each API endpoint fails independently.
- Licensed under the [MIT License](LICENSE). Bundled exception:
  `assets/bt_bold.ttf` is an ASCII subset of Roboto Bold (Apache License
  2.0), rendered via LVGL tiny_ttf so values can scale to any size. If
  the build lacks tiny_ttf the app falls back to the builtin Montserrat
  sizes (max 28 px).
