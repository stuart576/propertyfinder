"""Base parser with shared extraction utilities."""
import re
from typing import Optional
from bs4 import BeautifulSoup

import config


class BaseParser:
    """Base class for property alert email parsers."""

    source_name: str = "unknown"

    def parse(self, html: str, text: str = "") -> list[dict]:
        """
        Parse an alert email and return a list of property dicts.
        Each dict should have: title, price, bedrooms, acres, location, county, url, image_url, description, source
        """
        raise NotImplementedError

    # ── Shared utilities ──

    @staticmethod
    def extract_price(text: str) -> Optional[int]:
        """Extract price from text like '£650,000' or '£1,250,000' or 'Guide Price £500,000'."""
        if not text:
            return None
        patterns = [
            r"£\s*([\d,]+(?:\.\d+)?)\s*(?:k|K)",        # £650k
            r"£\s*([\d,]+(?:\.\d+)?)",                    # £650,000
            r"([\d,]+(?:\.\d+)?)\s*(?:k|K)\b",            # 650k without £
        ]
        for pattern in patterns:
            match = re.search(pattern, text.replace(",", ""))
            if match:
                val = float(match.group(1).replace(",", ""))
                # If the value looks like it's in thousands (e.g. 650k)
                if "k" in text.lower() and val < 10000:
                    val *= 1000
                return int(val) if val > 1000 else None
        return None

    @staticmethod
    def extract_bedrooms(text: str) -> Optional[int]:
        """Extract bedroom count from text."""
        if not text:
            return None
        patterns = [
            r"(\d+)\s*(?:bed(?:room)?s?)\b",
            r"(\d+)\s*(?:br|BR)\b",
            r"\b(\d+)\s*bed\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                count = int(match.group(1))
                if 1 <= count <= 20:
                    return count
        return None

    @staticmethod
    def extract_acres(text: str) -> Optional[float]:
        """Extract acreage from text like '5.2 acres' or 'approximately 3 acres'."""
        if not text:
            return None
        patterns = [
            r"([\d.]+)\s*acres?\b",
            r"approx(?:imately)?\s*([\d.]+)\s*acres?\b",
            r"circa\s*([\d.]+)\s*acres?\b",
            r"about\s*([\d.]+)\s*acres?\b",
            r"([\d.]+)\s*acre\(?s?\)?\s*(?:of\s+)?(?:land|ground|paddock|pasture)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = float(match.group(1))
                if 0.1 <= val <= 5000:
                    return val
        return None

    @staticmethod
    def detect_county(text: str) -> str:
        """Detect county from location text."""
        lower = text.lower()
        if any(w in lower for w in ["hereford", "hr1", "hr2", "hr3", "hr4", "hr5", "hr6", "hr7", "hr8", "hr9",
                                     "ross-on-wye", "leominster", "ledbury", "bromyard", "kington"]):
            return "herefordshire"
        if any(w in lower for w in ["worcester", "wr1", "wr2", "wr3", "wr4", "wr5", "wr6", "wr7", "wr8", "wr9",
                                     "malvern", "evesham", "droitwich", "pershore", "bromsgrove", "kidderminster",
                                     "bewdley", "tenbury"]):
            return "worcestershire"
        return ""

    def passes_filters(self, prop: dict) -> bool:
        """Check if a property passes the configured filters."""
        f = config.FILTERS

        # Price filter
        if prop.get("price") and prop["price"] > f["max_price"]:
            return False

        # Bedroom filter
        if prop.get("bedrooms") and prop["bedrooms"] < f["min_bedrooms"]:
            return False

        # County filter
        county = (prop.get("county") or "").lower()
        if county and county not in f["counties"]:
            return False

        return True

    @staticmethod
    def extract_postcode(text: str) -> str:
        """Extract a UK postcode from text. Returns first full postcode found, normalised."""
        if not text:
            return ""
        match = re.search(
            r'\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*(\d[A-Z]{2})\b',
            text.upper(),
        )
        if match:
            return f"{match.group(1)} {match.group(2)}"
        return ""

    @staticmethod
    def extract_postcode_from_fields(title: str, location: str, description: str) -> str:
        """Try to extract a postcode from multiple text fields."""
        for text in [location, title, description]:
            pc = BaseParser.extract_postcode(text)
            if pc:
                return pc
        return ""

    def _extract_location(self, text: str) -> str:
        """Extract location from listing text."""
        patterns = [
            r"(?:in|at|near)\s+([A-Z][a-zA-Z\s\-]+,\s*(?:Herefordshire|Worcestershire))",
            r"([A-Z][a-zA-Z\s\-]+,\s*(?:Hereford|Worcester|Ross-on-Wye|Leominster|Ledbury|Malvern|Bromyard|Evesham))",
            r"\b(HR\d+\s+\d[A-Z]{2})\b",
            r"\b(WR\d+\s+\d[A-Z]{2})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return self.clean_text(match.group(1))
        return ""

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean up extracted text."""
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"^\s*[·•\-–—]\s*", "", text)
        return text
