"""Parser for Rightmove property alert emails."""
import re
from bs4 import BeautifulSoup
from parsers.base import BaseParser


class RightmoveParser(BaseParser):
    source_name = "rightmove"

    def parse(self, html: str, text: str = "") -> list[dict]:
        properties = []
        soup = BeautifulSoup(html, "html.parser")

        # Rightmove alert emails contain property cards as table cells or divs
        # with links back to rightmove.co.uk/properties/...

        # Strategy 1: Find all rightmove property links
        links = soup.find_all("a", href=re.compile(r"rightmove\.co\.uk.*(?:property|properties)", re.IGNORECASE))

        seen_urls = set()
        for link in links:
            url = link.get("href", "").split("?")[0].strip()
            if not url or url in seen_urls:
                continue
            # Skip non-listing links
            if "/property-for-sale/" in url and "/find" in url:
                continue
            seen_urls.add(url)

            # Try to extract details from the surrounding context
            # Walk up to find the containing cell/div
            container = link
            for _ in range(8):
                if container.parent:
                    container = container.parent
                    container_text = container.get_text(separator=" ", strip=True)
                    if len(container_text) > 50:
                        break

            full_text = container.get_text(separator=" ", strip=True) if container else ""

            # Extract image — prefer property photos over icons/logos
            image_url = ""
            if container:
                # Try: img with "property-photo" in src/data-src
                for img in container.find_all("img"):
                    src = img.get("src", "") or img.get("data-src", "")
                    if "property-photo" in src.lower():
                        image_url = src
                        break
                # Fallback: img with alt="image" (Rightmove convention)
                if not image_url:
                    img = container.find("img", alt=re.compile(r"^image$", re.IGNORECASE))
                    if img:
                        image_url = img.get("src", "") or img.get("data-src", "")
                # Fallback: any img
                if not image_url:
                    img = container.find("img")
                    if img:
                        image_url = img.get("src", "") or img.get("data-src", "")

            price = self.extract_price(full_text)
            bedrooms = self.extract_bedrooms(full_text)
            acres = self.extract_acres(full_text)
            county = self.detect_county(full_text)

            # Extract title - usually the first meaningful text
            title_text = ""
            if link.get_text(strip=True):
                title_text = self.clean_text(link.get_text(strip=True))
            if not title_text or len(title_text) < 10:
                title_text = self.clean_text(full_text[:200])

            prop = {
                "source": self.source_name,
                "title": title_text,
                "price": price,
                "bedrooms": bedrooms,
                "acres": acres,
                "location": self._extract_location(full_text),
                "county": county,
                "url": url,
                "image_url": image_url,
                "description": self.clean_text(full_text[:500]),
            }

            if self.passes_filters(prop):
                properties.append(prop)

        # Strategy 2: If no links found, try parsing from plain text
        if not properties and text:
            properties = self._parse_text_fallback(text)

        return properties

    def _extract_location(self, text: str) -> str:
        """Try to extract a location from listing text."""
        # Look for common Rightmove location patterns
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

    def _parse_text_fallback(self, text: str) -> list[dict]:
        """Parse from plain text email as fallback."""
        properties = []
        # Split by price markers
        chunks = re.split(r"(?=£[\d,]+)", text)
        for chunk in chunks:
            if len(chunk) < 30:
                continue
            price = self.extract_price(chunk)
            if not price:
                continue
            url_match = re.search(r"(https?://[^\s]+rightmove[^\s]+)", chunk)
            url = url_match.group(1) if url_match else ""

            prop = {
                "source": self.source_name,
                "title": self.clean_text(chunk[:150]),
                "price": price,
                "bedrooms": self.extract_bedrooms(chunk),
                "acres": self.extract_acres(chunk),
                "location": self._extract_location(chunk),
                "county": self.detect_county(chunk),
                "url": url,
                "image_url": "",
                "description": self.clean_text(chunk[:500]),
            }
            if self.passes_filters(prop):
                properties.append(prop)

        return properties
