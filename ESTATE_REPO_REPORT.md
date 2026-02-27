# Estate Sale Arbitrage Scanner — Repository Report

**Repository:** https://github.com/tien1868/estate
**Report Date:** February 27, 2026
**Project Version:** 0.1.0

---

## Overview

**estate-arb** is a Python CLI application that automates finding underpriced items at estate sales and cross-references them against eBay sold prices to identify arbitrage (flip) opportunities. It combines web scraping, AI-powered photo analysis (Claude Vision via AWS Bedrock), and statistical price analysis to surface profitable finds.

**Tagline:** *Estate sale arbitrage finder — AI-powered scouting with eBay price cross-referencing*

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python >= 3.11 |
| Web Scraping | Playwright (headless Chromium) |
| AI Vision | Claude Sonnet 4 via AWS Bedrock (boto3) |
| Photo Downloads | aiohttp (async concurrent) |
| Terminal UI | Rich (panels, tables, progress bars) |
| HTML Parsing | BeautifulSoup4 |
| Deployment | Vercel (static HTML report) |

---

## File Tree (25 files)

```
.gitignore
pyproject.toml
vercel.json
config/
  brands.json          # 21 brand configurations across 8 categories
  settings.json        # Default: ZIP 80229 (Denver), 40mi radius, 2x profit min
public/
  index.html           # Generated HTML report (overwritten each scan)
src/estate_arb/
  __init__.py
  cli.py               # Main entry point — 6-phase pipeline orchestrator
  config.py            # BrandConfig + Settings dataclasses, JSON loaders
  cache/
    disk_cache.py       # Key-value disk cache with TTL (referenced but missing from repo)
  matching/
    __init__.py
    brand_matcher.py    # Regex-based brand detection in sale descriptions
    price_analyzer.py   # eBay price statistics with 2-sigma outlier removal
  models/
    __init__.py
    ebay_listing.py     # EbaySoldListing dataclass
    estate_sale.py      # EstateSale + SaleItem dataclasses
    opportunity.py      # ArbitrageOpportunity with ROI calculation
  output/
    __init__.py
    html_report.py      # Self-contained dark-theme HTML dashboard generator
    terminal.py         # Rich terminal output with colored panels
  scrapers/
    __init__.py
    base.py             # BaseScraper: Playwright browser mgmt, rate limiting, retries
    ebay.py             # EbaySoldScraper: eBay completed listing scraper
    estatesales.py      # EstateSalesScraper: EstateSales.net scraper (API intercept + DOM fallback)
  vision/
    __init__.py
    photo_analyzer.py   # Claude Vision photo analysis via AWS Bedrock
```

---

## Pipeline Architecture (6 Phases)

### Phase 1: Scrape Estate Sales
- Loads EstateSales.net (Angular SPA) with Playwright
- **Dual strategy:** intercepts XHR/API JSON responses (primary) or parses rendered DOM (fallback)
- Enriches each sale with full descriptions and photo galleries
- Configurable: max 50 sales, 2s delay between requests

### Phase 2: Text-Based Brand Matching
- Pre-compiled regex patterns with word-boundary matching (`\b`)
- Scans sale descriptions for 21 configured brands
- Extracts price context (dollar amounts within 200 chars of brand mention)
- One match per brand per sale to avoid duplicates

### Phase 3: AI Vision Photo Analysis
- Downloads photos concurrently with aiohttp (15s timeout per photo)
- Sends to Claude Sonnet 4 via AWS Bedrock in batches of 10
- Detailed prompt covers: vintage denim, rare fabrics, designer labels, MTG cards, cookware, military gear
- Looks for hidden value indicators: "Made in USA/Japan/Italy", union tags, construction quality
- Deduplicates findings across photo batches
- Temperature: 0.2 for consistent analysis

### Phase 4: eBay Price Cross-Reference
- Scrapes eBay completed/sold listings (`LH_Sold=1&LH_Complete=1`)
- Parses prices, shipping, conditions from search result cards
- Statistical analysis with 2-sigma outlier removal (5+ listings threshold)
- Disk caching (24hr TTL) to avoid redundant eBay lookups
- Matches filtered by minimum profit multiplier (default: 2x)

### Phase 5: Terminal Output
- Rich panels with color-coded borders (green = 2x+ profit, yellow = below)
- Displays: sale info, brand match, detection source, eBay median/avg/range, ROI

### Phase 6: HTML Report
- Self-contained HTML with inline CSS (dark theme) and vanilla JS
- Category auto-classification across 10 categories via keyword matching
- Interactive: category filters, text search, expandable detail rows
- Responsive design for mobile
- Optional Vercel deployment with `--deploy` flag

---

## Brand Configuration (21 Brands, 8 Categories)

| Category | Brands | Min eBay Price |
|----------|--------|----------------|
| Outdoor Heritage | Patagonia, The North Face, Mountain Hardwear, LL Bean | $15–$25 |
| Premium Menswear | Ralph Lauren, Brooks Brothers, Samuelsohn, Wallace & Barnes | $15–$30 |
| Designer Contemporary | Claude Montana, Nili Lotan, Frank & Eileen, Maje, Eileen Fisher, Assembly NY | $15–$50 |
| Japanese Artisan | Sou Sou Kyoto | $25 |
| Specialty | Mauviel (copper cookware), Qiviut (musk ox fiber) | $40–$50 |
| Vintage | Vintage Military, Vintage Leather | $30–$40 |
| Collectibles | Magic: The Gathering, Vintage NASCAR | $10–$20 |

---

## Default Settings

| Setting | Value |
|---------|-------|
| ZIP Code | 80229 (Denver area) |
| Search Radius | 40 miles |
| Min Profit Multiplier | 2.0x |
| Max Estate Sales | 50 |
| Max Photos/Sale | 30 |
| Max eBay Pages | 3 |
| Cache TTL | 24 hours |
| Request Delay | 2.0s (estate), 3.0s (eBay) |
| AI Model | Claude Sonnet 4 (us.anthropic.claude-sonnet-4-20250514-v1:0) |
| Browser | Headless Chromium |

---

## CLI Usage

```
estate-arb --zip 07042 --state NJ --city Montclair
estate-arb --zip 10001 --multiplier 3.0 --visible
estate-arb --zip 07042 --state NJ --city Montclair --no-vision
estate-arb --zip 80229 --deploy
```

| Flag | Description |
|------|-------------|
| `--zip` | ZIP code to search near |
| `--state` | State abbreviation (e.g., NJ) |
| `--city` | City name |
| `--multiplier` | Minimum profit multiplier (default: 2.0) |
| `--brands` | Path to brands config (default: config/brands.json) |
| `--settings` | Path to settings config (default: config/settings.json) |
| `--no-cache` | Ignore cached eBay prices |
| `--no-vision` | Skip AI vision (faster, no API cost) |
| `--visible` | Show browser windows |
| `--verbose` / `-v` | Enable debug logging |
| `--deploy` | Deploy HTML report to Vercel |

---

## Data Models

### SaleItem
An item detected within an estate sale — from text matching or AI vision.
- `brand`, `description`, `estimated_price`, `confidence`
- `source`: "text", "vision", or "both"
- `item_type`, `reasoning`, `ebay_query`

### EstateSale
An estate sale listing from EstateSales.net.
- Sale metadata: `sale_id`, `title`, `organizer`, `address`, `city/state/zip`, `url`
- Content: `description`, `photo_urls`, `dates`
- Results: `matched_items` (list of SaleItem)

### EbaySoldListing
A single eBay completed/sold listing.
- `title`, `sold_price`, `sold_date`, `condition`, `url`, `shipping_cost`
- Computed `total_price` property (price + shipping)

### ArbitrageOpportunity
Combined estate + eBay data for a single arbitrage opportunity.
- Estate: `title`, `url`, `dates`, `location`, `price_estimate`
- Match: `brand`, `description`, `detection_source`, `item_type`, `vision_reasoning`
- eBay: `median_sold`, `average_sold`, `sample_count`, `price_range`
- ROI: `profit_multiplier`, `estimated_roi_pct` (computed property)

---

## Bugs & Issues Found

### 1. Missing Module: `cache/disk_cache.py`
`cli.py` imports `from .cache.disk_cache import DiskCache` but the `cache/` directory does not exist in the repository. The application will crash on startup without this module.

**Expected interface:**
```python
cache = DiskCache(ttl_hours=24)
cache.get(query)      # returns dict or None
cache.set(query, stats)  # stores dict
```

### 2. Duplicate Row in Terminal Summary (`output/terminal.py`)
The `display_summary()` method has a duplicate line:
```python
summary.add_row("AI vision discoveries", str(vision_finds))
summary.add_row("AI vision discoveries", str(vision_finds))  # duplicate
```
This causes "AI vision discoveries" to appear twice in the scan summary.

### 3. `beautifulsoup4` Listed but Not Used
`beautifulsoup4` is declared as a dependency in `pyproject.toml` but is never imported anywhere in the codebase. The scraping is done entirely through Playwright's DOM API and JavaScript evaluation.

---

## Architecture Strengths

1. **Dual scraping strategy** — API intercept as primary with DOM fallback handles the Angular SPA reliably
2. **AI Vision for hidden value** — Goes beyond text matching to find items estate sale organizers may have underpriced
3. **Statistical price analysis** — Outlier removal prevents skewed results from anomalous eBay listings
4. **Caching** — Disk cache with TTL avoids redundant eBay lookups across runs
5. **Self-contained HTML reports** — No external dependencies, deployable to Vercel
6. **Clean async architecture** — Proper use of async context managers, concurrent downloads

---

## Sample Report Output

The existing `public/index.html` contains a live example showing:
- **50 estate sales scanned** near Denver, CO (ZIP 80229)
- **3 arbitrage opportunities** found
  - Western Saddle — $370 eBay median
  - Looney Tunes Animation Cels — $274 eBay median
  - Tiffany & Co Sterling Silver Pendant — $189 eBay median, $15 estate price (12.6x profit multiplier)

---

*Report generated from source code analysis of https://github.com/tien1868/estate*
