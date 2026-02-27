"""StorageTreasures.com scraper for VAULT.

Scrapes public auction listings including unit details, photos, and final bids.
Uses polite delays and respects robots.txt.

NOTE: You will likely need to adjust CSS selectors once you see the actual
HTML structure of StorageTreasures.com. The selectors below are based on
common auction site patterns and may need tuning.
"""

import re
import time
import random
import urllib.robotparser
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from vault.config import get_config
from vault.logger import setup_logger
from storage.database import Database
from storage.images import save_image

log = setup_logger("vault.scraper")


class StorageTreasuresScraper:
    """Scraper for StorageTreasures.com public auction listings."""

    def __init__(self):
        cfg = get_config()["scraping"]
        self.base_url = cfg["base_url"]
        self.listings_path = cfg["listings_path"]
        self.delay_min = cfg["delay_min"]
        self.delay_max = cfg["delay_max"]
        self.max_retries = cfg["max_retries"]
        self.retry_backoff = cfg["retry_backoff"]
        self.max_pages = cfg["max_pages"]
        self.respect_robots = cfg["respect_robots_txt"]

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

        self.db = Database()
        self.robots_parser = None

    def _polite_delay(self):
        """Wait a random delay between requests to be respectful."""
        delay = random.uniform(self.delay_min, self.delay_max)
        log.debug(f"Waiting {delay:.1f}s before next request")
        time.sleep(delay)

    def _check_robots(self, url):
        """Check robots.txt for the given URL."""
        if not self.respect_robots:
            return True

        if self.robots_parser is None:
            self.robots_parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{self.base_url}/robots.txt"
            try:
                self.robots_parser.set_url(robots_url)
                self.robots_parser.read()
                log.info(f"Loaded robots.txt from {robots_url}")
            except Exception as e:
                log.warning(f"Could not load robots.txt: {e}. Proceeding cautiously.")
                return True

        allowed = self.robots_parser.can_fetch(
            self.session.headers["User-Agent"], url
        )
        if not allowed:
            log.warning(f"robots.txt disallows: {url}")
        return allowed

    def _fetch_page(self, url):
        """Fetch a page with retry logic."""
        if not self._check_robots(url):
            return None

        for attempt in range(1, self.max_retries + 1):
            try:
                self._polite_delay()
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                return BeautifulSoup(response.text, "lxml")
            except requests.RequestException as e:
                wait = self.retry_backoff ** attempt
                log.warning(f"Request failed (attempt {attempt}/{self.max_retries}): {e}")
                if attempt < self.max_retries:
                    log.info(f"Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    log.error(f"Failed to fetch {url} after {self.max_retries} attempts")
                    return None

    def _download_image(self, img_url, auction_id, photo_index):
        """Download an image and save it compressed."""
        try:
            self._polite_delay()
            response = self.session.get(img_url, timeout=30)
            response.raise_for_status()
            local_path = save_image(response.content, auction_id, photo_index)
            return local_path
        except Exception as e:
            log.error(f"Failed to download image {img_url}: {e}")
            return None

    def scrape_listings_page(self, page_num=1):
        """Scrape a single page of auction listings.

        Returns list of auction summary dicts with URLs for detail pages.

        NOTE: These selectors need to be adjusted to match the actual
        StorageTreasures.com HTML structure. Run once and inspect the
        output to tune them.
        """
        url = f"{self.base_url}{self.listings_path}?page={page_num}"
        log.info(f"Scraping listings page {page_num}: {url}")

        soup = self._fetch_page(url)
        if not soup:
            return []

        listings = []

        # --- SELECTOR TUNING ZONE ---
        # These selectors are approximations. Inspect the actual HTML and adjust.
        # Common patterns for auction listing sites:
        auction_cards = soup.select(
            ".auction-card, .listing-card, .auction-item, "
            "[data-auction-id], .search-result-item, .auction-listing"
        )

        if not auction_cards:
            # Fallback: try broader selectors
            auction_cards = soup.select(".card, .listing, .result-item, article")
            log.warning(
                f"Primary selectors found 0 results. Fallback found {len(auction_cards)} cards. "
                "You may need to adjust CSS selectors in scraper.py."
            )

        for card in auction_cards:
            try:
                listing = self._parse_listing_card(card)
                if listing and listing.get("auction_id"):
                    listings.append(listing)
            except Exception as e:
                log.warning(f"Failed to parse listing card: {e}")

        log.info(f"Found {len(listings)} listings on page {page_num}")
        return listings

    def _parse_listing_card(self, card):
        """Parse a single auction listing card from the listings page.

        NOTE: Adjust these selectors based on actual HTML structure.
        """
        listing = {}

        # Try to extract auction ID from URL or data attribute
        link = card.select_one("a[href*='auction'], a[href*='unit'], a.card-link, a")
        if link:
            href = link.get("href", "")
            listing["auction_url"] = urljoin(self.base_url, href)

            # Extract ID from URL path (e.g., /auctions/12345 or /auction/detail/12345)
            id_match = re.search(r'/(\d{4,})', href)
            if id_match:
                listing["auction_id"] = id_match.group(1)

        # Data attribute fallback for ID
        if not listing.get("auction_id"):
            listing["auction_id"] = card.get("data-auction-id") or card.get("data-id")

        if not listing.get("auction_id"):
            return None

        # Facility name
        facility_el = card.select_one(
            ".facility-name, .storage-facility, .location-name, h3, h4, .card-title"
        )
        listing["facility_name"] = facility_el.get_text(strip=True) if facility_el else "Unknown"

        # Location
        location_el = card.select_one(
            ".location, .address, .city-state, .facility-address, .card-subtitle"
        )
        listing["location"] = location_el.get_text(strip=True) if location_el else ""

        # Unit size
        size_el = card.select_one(".unit-size, .size, .dimensions")
        if size_el:
            listing["unit_size"] = size_el.get_text(strip=True)
        else:
            # Try to find size in text content
            text = card.get_text()
            size_match = re.search(r'(\d+)\s*[xX×]\s*(\d+)', text)
            if size_match:
                listing["unit_size"] = f"{size_match.group(1)}x{size_match.group(2)}"
            else:
                listing["unit_size"] = ""

        # Auction end date
        date_el = card.select_one(
            ".auction-date, .end-date, .countdown, time, [data-end-time]"
        )
        if date_el:
            listing["auction_end_date"] = (
                date_el.get("datetime")
                or date_el.get("data-end-time")
                or date_el.get_text(strip=True)
            )
        else:
            listing["auction_end_date"] = ""

        # Current/final bid
        bid_el = card.select_one(
            ".current-bid, .bid-amount, .price, .winning-bid, .final-bid"
        )
        if bid_el:
            bid_text = bid_el.get_text(strip=True)
            bid_match = re.search(r'[\$]?([\d,]+\.?\d*)', bid_text)
            listing["final_bid"] = float(bid_match.group(1).replace(",", "")) if bid_match else None
        else:
            listing["final_bid"] = None

        # Photo count
        photos = card.select("img.auction-photo, img.unit-photo, .photo-count, img")
        listing["num_photos"] = len(photos) if photos else 0

        # Status
        status_el = card.select_one(".status, .auction-status, .badge")
        if status_el:
            status_text = status_el.get_text(strip=True).lower()
            if "closed" in status_text or "ended" in status_text or "sold" in status_text:
                listing["status"] = "closed"
            else:
                listing["status"] = "active"
        else:
            listing["status"] = "active"

        return listing

    def scrape_auction_detail(self, auction_url, auction_id):
        """Scrape the detail page of a single auction for photos and extra info.

        NOTE: Adjust selectors based on actual detail page HTML.
        """
        log.info(f"Scraping detail page: {auction_url}")
        soup = self._fetch_page(auction_url)
        if not soup:
            return None

        detail = {"auction_id": auction_id}

        # Get all unit photos
        photo_urls = []

        # Try various image selectors
        photo_elements = soup.select(
            ".auction-photos img, .gallery img, .unit-images img, "
            ".photo-gallery img, .carousel img, .slider img, "
            "[data-gallery] img, .lightbox img"
        )

        for img in photo_elements:
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if src and not self._is_icon_or_logo(src):
                photo_urls.append(urljoin(self.base_url, src))

        # Deduplicate while preserving order
        seen = set()
        unique_photos = []
        for url in photo_urls:
            if url not in seen:
                seen.add(url)
                unique_photos.append(url)
        detail["photo_urls"] = unique_photos

        # Try to get more detailed info from the detail page
        # Final bid (more accurate on detail page for closed auctions)
        bid_el = soup.select_one(
            ".winning-bid, .final-bid, .sold-price, .current-bid, .bid-amount"
        )
        if bid_el:
            bid_text = bid_el.get_text(strip=True)
            bid_match = re.search(r'[\$]?([\d,]+\.?\d*)', bid_text)
            if bid_match:
                detail["final_bid"] = float(bid_match.group(1).replace(",", ""))

        # Unit size (detail page may have more info)
        size_el = soup.select_one(
            ".unit-details .size, .unit-size, .storage-size, .unit-info"
        )
        if size_el:
            detail["unit_size"] = size_el.get_text(strip=True)

        # Facility details
        facility_el = soup.select_one(
            ".facility-name, .storage-facility, h1, .detail-title"
        )
        if facility_el:
            detail["facility_name"] = facility_el.get_text(strip=True)

        location_el = soup.select_one(
            ".facility-address, .location, .address"
        )
        if location_el:
            detail["location"] = location_el.get_text(strip=True)

        return detail

    def _is_icon_or_logo(self, src):
        """Filter out icons, logos, and other non-unit images."""
        skip_patterns = [
            "logo", "icon", "favicon", "placeholder", "avatar",
            "sprite", "badge", "button", "arrow", "loading",
            "1x1", "pixel", "tracking", "banner", "ad-"
        ]
        src_lower = src.lower()
        return any(p in src_lower for p in skip_patterns)

    def _has_next_page(self, soup):
        """Check if there's a next page of results."""
        next_link = soup.select_one(
            ".pagination .next, a[rel='next'], .next-page, "
            ".pagination a:last-child, a.page-link[aria-label='Next']"
        )
        if next_link:
            # Make sure it's not disabled
            parent = next_link.parent
            if parent and "disabled" in (parent.get("class") or []):
                return False
            return True
        return False

    def run_scrape(self, mode="listings"):
        """Main scraping workflow.

        Args:
            mode: "listings" to scrape listing pages and details,
                  "update_bids" to re-check closed auctions for final bids.
        """
        log.info(f"Starting scrape run (mode: {mode})")

        with self.db as db:
            if mode == "listings":
                self._scrape_all_listings(db)
            elif mode == "update_bids":
                self._update_final_bids(db)
            else:
                log.error(f"Unknown scrape mode: {mode}")

    def _scrape_all_listings(self, db):
        """Scrape all listing pages and their detail pages."""
        total_new = 0
        total_updated = 0

        for page in range(1, self.max_pages + 1):
            listings = self.scrape_listings_page(page)

            if not listings:
                log.info(f"No more listings found at page {page}. Stopping.")
                break

            for listing in listings:
                auction_id = listing["auction_id"]
                existing = db.get_auction(auction_id)

                # Scrape detail page for photos
                if listing.get("auction_url"):
                    detail = self.scrape_auction_detail(
                        listing["auction_url"], auction_id
                    )

                    if detail:
                        # Merge detail info into listing
                        for key in ["final_bid", "unit_size", "facility_name", "location"]:
                            if detail.get(key):
                                listing[key] = detail[key]

                        # Download photos
                        photo_urls = detail.get("photo_urls", [])
                        listing["num_photos"] = len(photo_urls)

                        for idx, photo_url in enumerate(photo_urls):
                            local_path = self._download_image(
                                photo_url, auction_id, idx
                            )
                            if local_path:
                                db.add_photo(auction_id, photo_url, local_path, idx)

                # Save to database
                db.upsert_auction(listing)

                if existing:
                    total_updated += 1
                else:
                    total_new += 1

            log.info(f"Page {page} complete. New: {total_new}, Updated: {total_updated}")

        log.info(
            f"Scrape complete. Total new: {total_new}, Total updated: {total_updated}"
        )

    def _update_final_bids(self, db):
        """Re-scrape active auctions that may have closed to capture final bids."""
        active_auctions = db.get_auctions(status="active")
        log.info(f"Checking {len(active_auctions)} active auctions for final bids")

        updated = 0
        for auction in active_auctions:
            if not auction.get("auction_url"):
                continue

            detail = self.scrape_auction_detail(
                auction["auction_url"], auction["auction_id"]
            )

            if detail and detail.get("final_bid"):
                auction["final_bid"] = detail["final_bid"]
                auction["status"] = "closed"
                db.upsert_auction(auction)
                updated += 1
                log.info(
                    f"Updated auction {auction['auction_id']}: "
                    f"final bid ${detail['final_bid']:.2f}"
                )

        log.info(f"Bid update complete. Updated {updated} auctions with final bids.")
