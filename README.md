# Mr Burki Tarama

Saatlik breakout scanner — BIST + ABD + Crypto + Futures + Indices üzerinde O'Neil + Minervini + Weinstein hard filters + 100 puanlık scoring + action layer.

## Live dashboard

Bir kez `Settings → Pages`'de Source = "GitHub Actions" ayarlandıktan sonra:

[**https://USERNAME.github.io/mr-burki-tarama/**](https://USERNAME.github.io/mr-burki-tarama/)

## Nasıl çalışır

GitHub Actions her hafta içi:

- **06:30 UTC** (09:30 TR) — `daily refresh`: 873 ticker için 1y daily data, hard filters + disqualifiers, candidate pool
- **07:00–19:00 UTC her saat** — `hourly scan`: pool için intraday + scoring + action layer
- Sonuç `docs/index.html` (snapshot embed) ve `cache/*.json` olarak repo'ya commit edilir
- GitHub Pages otomatik deploy eder

`workflow_dispatch` ile manuel de tetikleyebilirsin (Actions sekmesi).

## Filter mantığı

**Hard filters (hepsini geçmeli):**

- Trend Template: Price &gt; 50DMA &gt; 150DMA &gt; 200DMA, 200DMA up 1m
- Stage 2: Price &gt; 30W MA, 30W MA slope positive
- Price within 40% of 52W high
- Price ≥ 15% above 52W low
- Liquidity (BIST: 50M TL, US: $5M, CRYPTO: $10M, FUTURE: 1000 contracts)
- Base length 5–65 hafta, depth 8–50%

Crypto/futures/indices'te Stage 2 weekly check gevşek.

**Disqualifiers:**

- Earnings within 5 trading days (sadece stocks)
- High-volume gap-down &gt;5% in last 20 days

**Scoring (100 puan):**

- Volume quality (25, gating)
- Relative strength vs benchmark (20)
- Base & pattern quality (15)
- Trend quality (15)
- Breakout quality (15)
- Momentum (10)

**Action layer:**

- Score 80+ + fresh breakout → STRONG BUY
- Score 80+ + pivot ≤3% yakın → PRIME WATCH
- Score 65+ + fresh → BUY
- Score 65+ + pivot ≤5% yakın → WATCH
- Score 50+ → MONITOR
- Score &lt;50 → PASS

## Lokal geliştirme

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
SCANNER_BASE=$(pwd) python run_daily.py
SCANNER_BASE=$(pwd) python run_hourly.py --force
SCANNER_BASE=$(pwd) python build_pages.py
open docs/index.html
```

## Dosyalar

- `hisseler.txt` — taranacak ticker listesi (BIST `.IS`, US bare, crypto `-USD`, futures `=F`, indices `^`)
- `scanner_core.py` — filters + scoring
- `data_fetch.py` — yfinance + cache
- `build_pages.py` — static HTML üretici (GitHub Pages için)
- `.github/workflows/scanner.yml` — cron + commit
- `docs/index.html` — public dashboard (Actions tarafından üretilir)
