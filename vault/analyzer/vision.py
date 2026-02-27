"""Vision analysis pipeline using Google Gemini 2.0 Flash.

Analyzes storage unit photos to extract features that correlate with auction value.
"""

import json
import time
import base64
from pathlib import Path

import google.generativeai as genai

from vault.config import get_config
from vault.logger import setup_logger
from storage.database import Database
from storage.images import get_auction_images, load_image_bytes

log = setup_logger("vault.analyzer")

# The structured prompt for Gemini vision analysis
ANALYSIS_PROMPT = """You are analyzing photos of a storage unit being auctioned.
Examine all provided images carefully and extract the following information.

Return your analysis as a JSON object with exactly these fields:

{
    "item_categories": ["list of visible item categories from: furniture, electronics, clothing, boxes, appliances, bikes, tools, toys, sports_equipment, artwork, musical_instruments, office_supplies, kitchenware, outdoor_equipment, luggage, other"],
    "packing_quality_score": <integer 1-10, where 1=completely chaotic/thrown in, 5=average, 10=meticulously organized and packed>,
    "box_types": ["list from: uhaul_branded, plastic_totes, garbage_bags, misc_cardboard, moving_boxes, storage_bins, none_visible"],
    "fill_percentage": <integer 0-100, estimate of how full the unit is>,
    "brand_names": ["list of any visible brand names or labels you can read"],
    "high_value_indicators": ["list of specific items or signs suggesting high value, e.g., 'matching furniture set', 'sealed boxes', 'electronics visible', 'professional moving supplies'"],
    "clothing_visible": <true or false>,
    "clothing_volume_estimate": "<one of: none, small_amount, moderate, large_amount, primarily_clothing>",
    "condition_assessment": "<one of: excellent, good, fair, poor, very_poor>",
    "overall_notes": "<brief 1-2 sentence description of what you see and your impression of unit value>"
}

IMPORTANT:
- Return ONLY the JSON object, no other text.
- Be conservative in your estimates - only report what you can clearly see.
- If you cannot determine something, use reasonable defaults (empty list, 0, false).
- For fill_percentage, consider the visible depth and height of items relative to unit size.
"""


class VisionAnalyzer:
    """Analyzes storage unit photos using Gemini 2.0 Flash vision."""

    def __init__(self):
        cfg = get_config()
        analysis_cfg = cfg["analysis"]
        api_key = cfg.get("gemini_api_key", "")

        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY not set. Add it to your .env file."
            )

        genai.configure(api_key=api_key)

        self.model = genai.GenerativeModel(analysis_cfg["model"])
        self.generation_config = genai.types.GenerationConfig(
            max_output_tokens=analysis_cfg["max_tokens"],
            temperature=analysis_cfg["temperature"],
        )
        self.rate_limit_delay = analysis_cfg.get("rate_limit_delay", 1.0)
        self.db = Database()

    def analyze_auction(self, auction_id):
        """Analyze all photos for a single auction and return extracted features."""
        image_paths = get_auction_images(auction_id)

        if not image_paths:
            log.warning(f"No images found for auction {auction_id}")
            return None

        log.info(f"Analyzing auction {auction_id} ({len(image_paths)} photos)")

        # Build the multimodal prompt with all images
        prompt_parts = [ANALYSIS_PROMPT]

        for img_path in image_paths:
            img_bytes = load_image_bytes(img_path)
            if img_bytes:
                prompt_parts.append({
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(img_bytes).decode("utf-8"),
                })

        if len(prompt_parts) == 1:
            log.warning(f"No valid images loaded for auction {auction_id}")
            return None

        try:
            response = self.model.generate_content(
                prompt_parts,
                generation_config=self.generation_config,
            )

            # Rate limit
            time.sleep(self.rate_limit_delay)

            # Parse the JSON response
            features = self._parse_response(response.text, auction_id)
            return features

        except Exception as e:
            log.error(f"Gemini API error for auction {auction_id}: {e}")
            return None

    def _parse_response(self, response_text, auction_id):
        """Parse the Gemini response into a structured features dict."""
        try:
            # Clean up response - strip markdown code blocks if present
            text = response_text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]  # Remove first line
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)

            # Normalize into database-ready format
            features = {
                "auction_id": auction_id,
                "item_categories": json.dumps(data.get("item_categories", [])),
                "packing_quality_score": float(data.get("packing_quality_score", 5)),
                "box_types": json.dumps(data.get("box_types", [])),
                "fill_percentage": float(data.get("fill_percentage", 0)),
                "brand_names": json.dumps(data.get("brand_names", [])),
                "high_value_indicators": json.dumps(data.get("high_value_indicators", [])),
                "clothing_visible": 1 if data.get("clothing_visible", False) else 0,
                "clothing_volume_estimate": data.get("clothing_volume_estimate", "none"),
                "condition_assessment": data.get("condition_assessment", "fair"),
                "raw_analysis": json.dumps(data),
            }

            log.info(
                f"Auction {auction_id}: "
                f"score={features['packing_quality_score']}, "
                f"fill={features['fill_percentage']}%, "
                f"categories={data.get('item_categories', [])}"
            )

            return features

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.error(f"Failed to parse Gemini response for {auction_id}: {e}")
            log.debug(f"Raw response: {response_text[:500]}")
            return None

    def analyze_single_image(self, image_bytes):
        """Analyze a single uploaded image (for dashboard scoring).

        Returns the raw parsed dict (not database format).
        """
        prompt_parts = [
            ANALYSIS_PROMPT,
            {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            },
        ]

        try:
            response = self.model.generate_content(
                prompt_parts,
                generation_config=self.generation_config,
            )

            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            return json.loads(text)

        except Exception as e:
            log.error(f"Gemini API error for single image analysis: {e}")
            return None

    def run_analysis(self):
        """Analyze all unanalyzed auctions in the database."""
        log.info("Starting vision analysis pipeline")

        with self.db as db:
            auctions = db.get_unanalyzed_auctions()
            log.info(f"Found {len(auctions)} auctions to analyze")

            analyzed = 0
            failed = 0

            for auction in auctions:
                auction_id = auction["auction_id"]
                features = self.analyze_auction(auction_id)

                if features:
                    db.save_features(features)
                    analyzed += 1
                    log.info(f"Saved features for auction {auction_id} ({analyzed}/{len(auctions)})")
                else:
                    failed += 1
                    log.warning(f"Failed to analyze auction {auction_id}")

            log.info(
                f"Analysis complete. Analyzed: {analyzed}, Failed: {failed}, "
                f"Total: {len(auctions)}"
            )
