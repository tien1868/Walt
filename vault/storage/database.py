"""SQLite database management for VAULT."""

import sqlite3
from pathlib import Path
from vault.config import get_db_path
from vault.logger import setup_logger

log = setup_logger("vault.storage")

SCHEMA_SQL = """
-- Auction sources (extensible for multiple sites)
CREATE TABLE IF NOT EXISTS auction_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Core auction listings
CREATE TABLE IF NOT EXISTS auctions (
    auction_id TEXT PRIMARY KEY,
    source_id INTEGER DEFAULT 1,
    facility_name TEXT,
    location TEXT,
    unit_size TEXT,
    num_photos INTEGER DEFAULT 0,
    final_bid REAL,
    auction_end_date TEXT,
    auction_url TEXT,
    status TEXT DEFAULT 'active',
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES auction_sources(id)
);

-- Photos linked to auctions
CREATE TABLE IF NOT EXISTS auction_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id TEXT NOT NULL,
    photo_url TEXT,
    local_path TEXT,
    photo_index INTEGER,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(auction_id)
);

-- Vision analysis results per auction
CREATE TABLE IF NOT EXISTS unit_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id TEXT NOT NULL UNIQUE,
    item_categories TEXT,
    packing_quality_score REAL,
    box_types TEXT,
    fill_percentage REAL,
    brand_names TEXT,
    high_value_indicators TEXT,
    clothing_visible INTEGER DEFAULT 0,
    clothing_volume_estimate TEXT,
    condition_assessment TEXT,
    raw_analysis TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (auction_id) REFERENCES auctions(auction_id)
);

-- Training results / model metadata
CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    num_samples INTEGER,
    accuracy REAL,
    top_features TEXT,
    model_path TEXT,
    notes TEXT
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_auctions_status ON auctions(status);
CREATE INDEX IF NOT EXISTS idx_auctions_end_date ON auctions(auction_end_date);
CREATE INDEX IF NOT EXISTS idx_auctions_source ON auctions(source_id);
CREATE INDEX IF NOT EXISTS idx_photos_auction ON auction_photos(auction_id);
CREATE INDEX IF NOT EXISTS idx_features_auction ON unit_features(auction_id);
"""


class Database:
    """SQLite database wrapper for VAULT."""

    def __init__(self, db_path=None):
        self.db_path = db_path or get_db_path()
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = None

    def connect(self):
        """Open database connection and initialize schema."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        log.info(f"Database connected: {self.db_path}")
        return self

    def _init_schema(self):
        """Create tables if they don't exist."""
        self.conn.executescript(SCHEMA_SQL)
        # Seed default auction source
        self.conn.execute(
            "INSERT OR IGNORE INTO auction_sources (id, name, base_url) VALUES (1, 'StorageTreasures', 'https://www.storagetreasures.com')"
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # --- Auction CRUD ---

    def upsert_auction(self, auction_data):
        """Insert or update an auction listing."""
        self.conn.execute("""
            INSERT INTO auctions (auction_id, facility_name, location, unit_size,
                                  num_photos, final_bid, auction_end_date, auction_url, status)
            VALUES (:auction_id, :facility_name, :location, :unit_size,
                    :num_photos, :final_bid, :auction_end_date, :auction_url, :status)
            ON CONFLICT(auction_id) DO UPDATE SET
                facility_name=excluded.facility_name,
                location=excluded.location,
                unit_size=excluded.unit_size,
                num_photos=excluded.num_photos,
                final_bid=COALESCE(excluded.final_bid, auctions.final_bid),
                auction_end_date=excluded.auction_end_date,
                auction_url=excluded.auction_url,
                status=excluded.status,
                updated_at=CURRENT_TIMESTAMP
        """, auction_data)
        self.conn.commit()

    def get_auction(self, auction_id):
        """Get a single auction by ID."""
        row = self.conn.execute(
            "SELECT * FROM auctions WHERE auction_id = ?", (auction_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_auctions(self, status=None, limit=100, offset=0):
        """Get auctions with optional status filter."""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM auctions WHERE status = ? ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM auctions ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_closed_auctions_with_bids(self):
        """Get all closed auctions that have final bid prices."""
        rows = self.conn.execute(
            "SELECT * FROM auctions WHERE final_bid IS NOT NULL AND final_bid > 0 ORDER BY final_bid DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unanalyzed_auctions(self):
        """Get auctions that have photos but no vision analysis yet."""
        rows = self.conn.execute("""
            SELECT a.* FROM auctions a
            LEFT JOIN unit_features uf ON a.auction_id = uf.auction_id
            WHERE uf.id IS NULL AND a.num_photos > 0
            ORDER BY a.scraped_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def count_auctions(self):
        """Get total auction count."""
        return self.conn.execute("SELECT COUNT(*) FROM auctions").fetchone()[0]

    # --- Photo CRUD ---

    def add_photo(self, auction_id, photo_url, local_path, photo_index):
        """Add a photo record for an auction."""
        self.conn.execute(
            "INSERT INTO auction_photos (auction_id, photo_url, local_path, photo_index) VALUES (?, ?, ?, ?)",
            (auction_id, photo_url, local_path, photo_index)
        )
        self.conn.commit()

    def get_photos(self, auction_id):
        """Get all photos for an auction."""
        rows = self.conn.execute(
            "SELECT * FROM auction_photos WHERE auction_id = ? ORDER BY photo_index",
            (auction_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Features CRUD ---

    def save_features(self, features):
        """Save vision analysis features for an auction."""
        self.conn.execute("""
            INSERT OR REPLACE INTO unit_features
            (auction_id, item_categories, packing_quality_score, box_types,
             fill_percentage, brand_names, high_value_indicators,
             clothing_visible, clothing_volume_estimate, condition_assessment, raw_analysis)
            VALUES (:auction_id, :item_categories, :packing_quality_score, :box_types,
                    :fill_percentage, :brand_names, :high_value_indicators,
                    :clothing_visible, :clothing_volume_estimate, :condition_assessment, :raw_analysis)
        """, features)
        self.conn.commit()

    def get_features(self, auction_id):
        """Get vision features for an auction."""
        row = self.conn.execute(
            "SELECT * FROM unit_features WHERE auction_id = ?", (auction_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_features_with_bids(self):
        """Get all features joined with auction bid data for training."""
        rows = self.conn.execute("""
            SELECT uf.*, a.final_bid, a.unit_size, a.location
            FROM unit_features uf
            JOIN auctions a ON uf.auction_id = a.auction_id
            WHERE a.final_bid IS NOT NULL AND a.final_bid > 0
        """).fetchall()
        return [dict(r) for r in rows]

    # --- Model Runs ---

    def save_model_run(self, run_data):
        """Save a training run record."""
        self.conn.execute("""
            INSERT INTO model_runs (num_samples, accuracy, top_features, model_path, notes)
            VALUES (:num_samples, :accuracy, :top_features, :model_path, :notes)
        """, run_data)
        self.conn.commit()

    def get_latest_model_run(self):
        """Get the most recent training run."""
        row = self.conn.execute(
            "SELECT * FROM model_runs ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # --- Stats ---

    def get_stats(self):
        """Get dashboard statistics."""
        stats = {}
        stats["total_auctions"] = self.conn.execute(
            "SELECT COUNT(*) FROM auctions"
        ).fetchone()[0]
        stats["analyzed_auctions"] = self.conn.execute(
            "SELECT COUNT(*) FROM unit_features"
        ).fetchone()[0]

        row = self.conn.execute(
            "SELECT AVG(final_bid), MAX(final_bid), MIN(final_bid) FROM auctions WHERE final_bid > 0"
        ).fetchone()
        stats["avg_bid"] = round(row[0], 2) if row[0] else 0
        stats["max_bid"] = round(row[1], 2) if row[1] else 0
        stats["min_bid"] = round(row[2], 2) if row[2] else 0

        stats["total_photos"] = self.conn.execute(
            "SELECT COUNT(*) FROM auction_photos"
        ).fetchone()[0]

        return stats
