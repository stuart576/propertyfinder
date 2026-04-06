"""Fetch property images from listing URLs via og:image meta tags."""
import logging
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger("property-finder.images")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_og_image(url: str, timeout: int = 10) -> str:
    """
    Fetch the og:image URL from a property listing page.
    Returns the image URL or empty string on failure.
    """
    if not url:
        return ""
    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", _USER_AGENT)
        with urlopen(req, timeout=timeout) as resp:
            # Read only the first 50KB — og:image is always in <head>
            chunk = resp.read(50_000).decode("utf-8", errors="ignore")

        # Look for og:image meta tag
        match = re.search(
            r'<meta\s+(?:[^>]*?)property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            chunk, re.IGNORECASE,
        )
        if not match:
            # Try reversed attribute order (content before property)
            match = re.search(
                r'<meta\s+(?:[^>]*?)content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
                chunk, re.IGNORECASE,
            )
        if match:
            img_url = match.group(1).strip()
            # Skip tiny placeholders / tracking pixels
            if any(skip in img_url.lower() for skip in ["1x1", "pixel", "spacer", "blank"]):
                return ""
            return img_url

    except (HTTPError, URLError, OSError, ValueError) as e:
        logger.debug(f"Could not fetch og:image from {url}: {e}")
    except Exception as e:
        logger.debug(f"Unexpected error fetching og:image from {url}: {e}")

    return ""
