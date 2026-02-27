"""Flask dashboard for VAULT - Storage Auction Intelligence System."""

import io
import json
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for
from PIL import Image

from vault.config import get_config, get_base_dir
from vault.logger import setup_logger
from storage.database import Database
from storage.images import get_auction_images

log = setup_logger("vault.dashboard")


def create_app():
    """Create and configure the Flask application."""
    cfg = get_config()

    app = Flask(
        __name__,
        template_folder=str(get_base_dir() / "dashboard" / "templates"),
        static_folder=str(get_base_dir() / "dashboard" / "static"),
    )
    app.secret_key = cfg.get("flask_secret_key", "dev-key")

    items_per_page = cfg["dashboard"]["items_per_page"]

    @app.route("/")
    def index():
        """Dashboard home — stats overview."""
        with Database() as db:
            stats = db.get_stats()
            recent = db.get_auctions(limit=5)

            # Get latest model run
            model_run = db.get_latest_model_run()

        return render_template(
            "index.html", stats=stats, recent=recent, model_run=model_run
        )

    @app.route("/auctions")
    def auctions():
        """Browse all scraped auctions."""
        page = request.args.get("page", 1, type=int)
        status = request.args.get("status", None)
        offset = (page - 1) * items_per_page

        with Database() as db:
            items = db.get_auctions(
                status=status, limit=items_per_page, offset=offset
            )
            total = db.count_auctions()

        total_pages = max(1, (total + items_per_page - 1) // items_per_page)

        return render_template(
            "auctions.html",
            auctions=items,
            page=page,
            total_pages=total_pages,
            status=status,
        )

    @app.route("/auction/<auction_id>")
    def auction_detail(auction_id):
        """Detail view for a single auction."""
        with Database() as db:
            auction = db.get_auction(auction_id)
            if not auction:
                return "Auction not found", 404

            photos = db.get_photos(auction_id)
            features = db.get_features(auction_id)

        # Parse JSON fields in features for display
        if features and features.get("raw_analysis"):
            try:
                features["parsed"] = json.loads(features["raw_analysis"])
            except json.JSONDecodeError:
                features["parsed"] = {}

        image_files = get_auction_images(auction_id)

        return render_template(
            "auction_detail.html",
            auction=auction,
            photos=photos,
            features=features,
            image_files=image_files,
        )

    @app.route("/insights")
    def insights():
        """Feature importance and correlation insights."""
        report_path = get_base_dir() / "data" / "reports" / "latest_report.json"
        report = None

        if report_path.exists():
            with open(report_path) as f:
                report = json.load(f)

        with Database() as db:
            model_run = db.get_latest_model_run()

        return render_template(
            "insights.html", report=report, model_run=model_run
        )

    @app.route("/score", methods=["GET", "POST"])
    def score_unit():
        """Upload a photo and get a predicted value range."""
        result = None
        error = None

        if request.method == "POST":
            file = request.files.get("photo")
            if not file or file.filename == "":
                error = "Please select a photo to upload."
            else:
                try:
                    image_bytes = file.read()

                    # Validate it's an image
                    img = Image.open(io.BytesIO(image_bytes))
                    img.verify()

                    # Re-read after verify
                    file.seek(0)
                    image_bytes = file.read()

                    # Run vision analysis
                    from analyzer.vision import VisionAnalyzer
                    analyzer = VisionAnalyzer()
                    features = analyzer.analyze_single_image(image_bytes)

                    if features:
                        # Run prediction
                        from trainer.model import ValuePredictor
                        predictor = ValuePredictor()
                        prediction = predictor.predict_value(features)

                        result = {
                            "features": features,
                            "prediction": prediction,
                        }
                    else:
                        error = "Could not analyze the image. Please try a different photo."

                except Exception as e:
                    log.error(f"Score error: {e}")
                    error = f"Analysis failed: {str(e)}"

        return render_template("score.html", result=result, error=error)

    @app.route("/api/stats")
    def api_stats():
        """JSON API endpoint for dashboard stats."""
        with Database() as db:
            stats = db.get_stats()
        return jsonify(stats)

    @app.route("/api/auctions")
    def api_auctions():
        """JSON API endpoint for auctions list."""
        limit = request.args.get("limit", 20, type=int)
        offset = request.args.get("offset", 0, type=int)
        status = request.args.get("status", None)

        with Database() as db:
            items = db.get_auctions(status=status, limit=limit, offset=offset)
        return jsonify(items)

    return app
