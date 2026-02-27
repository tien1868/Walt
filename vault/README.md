# VAULT — Storage Auction Intelligence System

Scrapes storage auction data, analyzes unit photos with AI vision, and learns what makes high-value units.

## Setup

### 1. Install Dependencies

```bash
cd vault
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and add your API key:

```
GEMINI_API_KEY=your_google_gemini_api_key
FLASK_SECRET_KEY=some-random-secret-string
```

Get a Gemini API key at https://aistudio.google.com/app/apikey

### 3. Review Configuration

Settings are in `config.yaml`. Defaults work out of the box — adjust scraping delays, storage paths, or model thresholds as needed.

## Usage

All commands run from the `vault/` directory:

```bash
# Scrape auction listings
python vault.py scrape

# Scrape with limited pages
python vault.py scrape --pages 5

# Update final bids on previously scraped active auctions
python vault.py scrape --update

# Run AI vision analysis on unprocessed auctions
python vault.py analyze

# Analyze a specific auction
python vault.py analyze --auction-id 12345

# Train the value prediction model
python vault.py train

# View database stats
python vault.py stats

# Launch the web dashboard
python vault.py serve

# Score a single image from CLI
python vault.py score path/to/photo.jpg
```

## Architecture

```
vault/
├── vault.py              # CLI entry point
├── config.yaml           # All configuration
├── .env                  # API keys (not committed)
├── requirements.txt      # Python dependencies
│
├── vault/                # Core package
│   ├── config.py         # Config loader
│   └── logger.py         # Logging setup
│
├── scraper/              # Web scraping
│   └── scraper.py        # StorageTreasures.com scraper
│
├── storage/              # Data layer
│   ├── database.py       # SQLite schema + CRUD operations
│   └── images.py         # Image download, compression, storage
│
├── analyzer/             # AI analysis
│   └── vision.py         # Gemini 2.0 Flash vision pipeline
│
├── trainer/              # ML training
│   └── model.py          # Feature correlation + value prediction
│
├── dashboard/            # Web UI
│   ├── app.py            # Flask application
│   └── templates/        # HTML templates
│
├── data/                 # Runtime data (gitignored)
│   ├── vault.db          # SQLite database
│   ├── images/           # Downloaded unit photos
│   └── reports/          # Generated JSON reports
│
└── logs/                 # Log files (gitignored)
```

## Workflow

### Phase 1: Scrape
Run `python vault.py scrape` to pull auction listings. The scraper collects unit metadata, downloads photos (compressed to 800px width), and stores everything in SQLite.

Run it in two passes:
1. First pass grabs active listings
2. Second pass (`--update`) re-checks closed auctions for final bid prices

### Phase 2: Analyze
Run `python vault.py analyze` to send unit photos through Gemini 2.0 Flash. The vision model extracts item categories, packing quality, fill percentage, brand names, clothing detection, and condition assessment.

### Phase 3: Train
Run `python vault.py train` once you have enough analyzed auctions with final bids (minimum 50 recommended). The model learns which visual features predict high-value units and generates a report.

### Phase 4: Dashboard
Run `python vault.py serve` to launch the web UI at http://localhost:5000 with:
- Stats overview
- Auction browser with photos and analysis
- Feature importance charts
- Upload-and-score tool for new unit photos

## Database Schema

The SQLite database (`data/vault.db`) has these tables:

- **auction_sources** — extensible for adding new auction sites
- **auctions** — core listing data (facility, location, size, bid, dates)
- **auction_photos** — photo URLs and local file paths
- **unit_features** — AI-extracted visual features per auction
- **model_runs** — training run history and accuracy metrics

## Scraper Notes

The CSS selectors in `scraper/scraper.py` are approximations based on common auction site patterns. You will need to:

1. Run the scraper once and check the logs
2. Inspect StorageTreasures.com's actual HTML in your browser
3. Adjust the selectors in `_parse_listing_card()` and `scrape_auction_detail()`

The scraper uses polite 2-5 second random delays between requests and respects robots.txt.

## Tech Stack

- Python 3.11+
- SQLite (via sqlite3)
- BeautifulSoup + Requests (scraping)
- Google Gemini 2.0 Flash (vision analysis)
- scikit-learn (ML correlation/prediction)
- Flask (dashboard)
- Pillow (image processing)
