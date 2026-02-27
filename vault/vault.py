#!/usr/bin/env python3
"""VAULT — Storage Auction Intelligence System

CLI interface for scraping, analyzing, training, and serving the dashboard.

Usage:
    python vault.py scrape             Scrape new auction listings
    python vault.py scrape --update    Re-check active auctions for final bids
    python vault.py analyze            Run vision analysis on unprocessed auctions
    python vault.py train              Train the value prediction model
    python vault.py serve              Launch the web dashboard
    python vault.py stats              Show database statistics
"""

import sys
import os
import json

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import click
from tabulate import tabulate

from vault.config import get_config
from vault.logger import setup_logger

log = setup_logger("vault.cli")


@click.group()
def cli():
    """VAULT — Storage Auction Intelligence System"""
    pass


@cli.command()
@click.option("--update", is_flag=True, help="Re-check active auctions for final bids")
@click.option("--pages", default=None, type=int, help="Max pages to scrape")
def scrape(update, pages):
    """Scrape storage auction listings from StorageTreasures.com."""
    from scraper.scraper import StorageTreasuresScraper

    scraper = StorageTreasuresScraper()

    if pages:
        scraper.max_pages = pages

    if update:
        log.info("Running bid update pass — checking active auctions for final prices")
        scraper.run_scrape(mode="update_bids")
    else:
        log.info("Running full listing scrape")
        scraper.run_scrape(mode="listings")


@cli.command()
@click.option("--auction-id", default=None, help="Analyze a specific auction only")
def analyze(auction_id):
    """Run Gemini vision analysis on scraped auction photos."""
    from analyzer.vision import VisionAnalyzer

    analyzer = VisionAnalyzer()

    if auction_id:
        from storage.database import Database
        with Database() as db:
            features = analyzer.analyze_auction(auction_id)
            if features:
                db.save_features(features)
                log.info(f"Analysis complete for auction {auction_id}")
                click.echo(json.dumps(features, indent=2))
            else:
                click.echo(f"Failed to analyze auction {auction_id}")
    else:
        analyzer.run_analysis()


@cli.command()
def train():
    """Train the value prediction model on analyzed auctions."""
    from trainer.model import ValuePredictor

    predictor = ValuePredictor()
    report = predictor.train()

    if report:
        click.echo("\n=== Training Report ===")
        click.echo(f"Dataset size: {report['dataset_size']}")
        click.echo(f"Average bid: ${report['avg_bid_overall']:.2f}")
        click.echo(f"R² score: {report['model_accuracy']['r2_score']:.4f}")
        click.echo(f"MAE: ${report['model_accuracy']['mean_absolute_error']:.2f}")

        click.echo("\nTop value indicators:")
        for feat in report["top_indicators"][:10]:
            name = feat["feature"].replace("cat_", "").replace("box_", "").replace("_", " ")
            click.echo(f"  {name:30s} {feat['importance']:.4f}")

        click.echo(f"\nFull report saved to data/reports/latest_report.json")
    else:
        click.echo("Training failed. Check logs for details.")


@cli.command()
@click.option("--host", default=None, help="Host to bind to")
@click.option("--port", default=None, type=int, help="Port to serve on")
def serve(host, port):
    """Launch the VAULT web dashboard."""
    from dashboard.app import create_app

    cfg = get_config()["dashboard"]
    app = create_app()

    run_host = host or cfg["host"]
    run_port = port or cfg["port"]

    click.echo(f"Starting VAULT dashboard at http://{run_host}:{run_port}")
    app.run(host=run_host, port=run_port, debug=cfg["debug"])


@cli.command()
def stats():
    """Show database statistics."""
    from storage.database import Database

    with Database() as db:
        s = db.get_stats()

    rows = [
        ["Total Auctions", s["total_auctions"]],
        ["Analyzed", s["analyzed_auctions"]],
        ["Total Photos", s["total_photos"]],
        ["Average Bid", f"${s['avg_bid']:.2f}" if s["avg_bid"] else "—"],
        ["Highest Bid", f"${s['max_bid']:.2f}" if s["max_bid"] else "—"],
        ["Lowest Bid", f"${s['min_bid']:.2f}" if s["min_bid"] else "—"],
    ]

    click.echo("\n=== VAULT Statistics ===")
    click.echo(tabulate(rows, headers=["Metric", "Value"], tablefmt="simple"))
    click.echo()


@cli.command()
@click.argument("image_path")
def score(image_path):
    """Score a single image from the command line.

    Usage: python vault.py score path/to/photo.jpg
    """
    from pathlib import Path

    path = Path(image_path)
    if not path.exists():
        click.echo(f"File not found: {image_path}")
        sys.exit(1)

    image_bytes = path.read_bytes()

    from analyzer.vision import VisionAnalyzer
    from trainer.model import ValuePredictor

    click.echo(f"Analyzing {image_path}...")
    analyzer = VisionAnalyzer()
    features = analyzer.analyze_single_image(image_bytes)

    if not features:
        click.echo("Failed to analyze image.")
        sys.exit(1)

    click.echo("\nDetected features:")
    click.echo(f"  Categories: {', '.join(features.get('item_categories', []))}")
    click.echo(f"  Packing quality: {features.get('packing_quality_score', '?')}/10")
    click.echo(f"  Fill: {features.get('fill_percentage', '?')}%")
    click.echo(f"  Condition: {features.get('condition_assessment', '?')}")
    click.echo(f"  Clothing: {'Yes' if features.get('clothing_visible') else 'No'}")

    if features.get("high_value_indicators"):
        click.echo(f"  High value signs: {', '.join(features['high_value_indicators'])}")

    predictor = ValuePredictor()
    prediction = predictor.predict_value(features)

    if prediction:
        click.echo(f"\nPredicted value: ${prediction['predicted_value']:.2f}")
        click.echo(
            f"Range: ${prediction['value_range_low']:.2f} — "
            f"${prediction['value_range_high']:.2f}"
        )
    else:
        click.echo("\nNo trained model available. Run 'python vault.py train' first.")


if __name__ == "__main__":
    cli()
