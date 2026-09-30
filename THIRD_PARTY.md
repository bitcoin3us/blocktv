# Third-party notices

BlockTV itself is GPL-3.0-or-later (see LICENSE). It ships the following
components under their own licences:

| component | files | licence |
|---|---|---|
| [zaptv-lib](https://github.com/bitcoin3us/zaptv-lib) — shared ZapTV-family modules | `blocktv_nostr_service.py`, `blocktv_zap_service.py`, `blocktv_market_data.py`, `blocktv_odometer.py`, `blocktv_field_picker.py`, `blocktv_clankertv_core.py`, `blocktv_clankertv_providers.py` (vendored with a `blocktv_` prefix; commit in `zaptv-lib.lock`) | MIT, Copyright (c) 2026 ZapTV.org |
| Lightning Piggy nostr service (via zaptv-lib) | most of `blocktv_nostr_service.py` | MIT, Copyright (c) 2025 MicroPythonOS (Thomas Farstrike) |
| Roboto Bold, ASCII subset | `bt_bold.ttf` | Apache License 2.0, Copyright 2011 Google Inc. |

Apache License 2.0 text: https://www.apache.org/licenses/LICENSE-2.0
