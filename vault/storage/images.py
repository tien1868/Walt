"""Image storage and processing for VAULT."""

import io
from pathlib import Path
from PIL import Image
from vault.config import get_config, get_images_dir
from vault.logger import setup_logger

log = setup_logger("vault.images")


def get_auction_image_dir(auction_id):
    """Get or create the image directory for an auction."""
    img_dir = get_images_dir() / str(auction_id)
    img_dir.mkdir(parents=True, exist_ok=True)
    return img_dir


def save_image(image_bytes, auction_id, photo_index):
    """Save and compress an image to the auction's image directory.

    Resizes to max width specified in config (default 800px) and saves as JPEG.
    Returns the local file path.
    """
    cfg = get_config()["storage"]
    max_width = cfg.get("max_image_width", 800)
    quality = cfg.get("image_quality", 85)

    img_dir = get_auction_image_dir(auction_id)
    filename = f"photo_{photo_index:03d}.jpg"
    filepath = img_dir / filename

    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Convert to RGB if necessary (handles PNG with alpha, etc.)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        # Resize if wider than max width
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.LANCZOS)

        img.save(filepath, "JPEG", quality=quality, optimize=True)
        log.debug(f"Saved image: {filepath} ({img.width}x{img.height})")
        return str(filepath)

    except Exception as e:
        log.error(f"Failed to save image for auction {auction_id} photo {photo_index}: {e}")
        return None


def load_image_bytes(filepath):
    """Load an image file and return bytes for API submission."""
    path = Path(filepath)
    if not path.exists():
        return None
    return path.read_bytes()


def get_auction_images(auction_id):
    """Get all image file paths for an auction, sorted by index."""
    img_dir = get_images_dir() / str(auction_id)
    if not img_dir.exists():
        return []
    return sorted(img_dir.glob("photo_*.jpg"))
