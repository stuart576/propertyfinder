"""Generic parser for specialist property sites and unknown email formats."""
import re
from bs4 import BeautifulSoup
from parsers.base import BaseParser


class GenericParser(BaseParser):
    source_name = "other"

    def parse(self, html: str, text: str = "") -> list[dict]:
        properties = []

        # Try HTML parsing first
        if html:
            properties = self._parse_html(html)

        # Fall back to text parsing
        if not properties and text:
            properties = self._parse_text(text)

        return properties

    def _parse_html(self, html: str) -> list[dict]:
        properties = []
        soup = BeautifulSoup(html, "html.parser")

        # Find all external links that look like property listings
        links = soup.find_all("a", href=re.compile(r"https?://", re.IGNORECASE))

        seen_urls = set()
        for link in links:
            url = link.get("href", "").strip()
            # Skip common non-listing links
            if any(skip in url.lower() for skip in [
                "unsubscribe", "manage", "preferences", "privacy",
                "terms", "footer", "header", "social", "facebook",
                "twitter", "instagram", "mailto:", "tel:"
            ]):
                continue

            clean_url = re.sub(r"[?&]utm_[^&]*", "", url).rstrip("?&")
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            # Find container text
            container = link
            for _ in range(6):
                if container.parent:
                    container = container.parent
                    container_text = container.get_text(separator=" ", strip=True)
                    if len(container_text) > 40:
                        break

            full_text = container.get_text(separator=" ", strip=True) if container else ""

            # Must have a price to be a property listing
            price = self.extract_price(full_text)
            if not price:
                continue

            # Determine source from URL
            source = self._detect_source(clean_url)

            img = container.find("img") if container else None
            image_url = img.get("src", "") or img.get("data-src", "") if img else ""

            prop = {
                "source": source,
                "title": self.clean_text(link.get_text(strip=True) or full_text[:150]),
                "price": price,
                "bedrooms": self.extract_bedrooms(full_text),
                "acres": self.extract_acres(full_text),
                "location": self._extract_location(full_text),
                "county": self.detect_county(full_text),
                "url": clean_url,
                "image_url": image_url,
                "description": self.clean_text(full_text[:500]),
            }

            if self.passes_filters(prop):
                properties.append(prop)

        return properties

    def _parse_text(self, text: str) -> list[dict]:
        properties = []

        # Split on price markers or URLs
        chunks = re.split(r"(?=£[\d,]+)|(?=https?://)", text)
        for chunk in chunks:
            if len(chunk) < 30:
                continue

            price = self.extract_price(chunk)
            if not price:
                continue

            url_match = re.search(r"(https?://[^\s]+)", chunk)
            url = url_match.group(1).rstrip(".,;)>") if url_match else ""
            source = self._detect_source(url) if url else "email"

            prop = {
                "source": source,
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

    @staticmethod
    def _detect_source(url: str) -> str:
        url_lower = url.lower()
        if "smallholding" in url_lower:
            return "smallholdingsforsale"
        elif "uklandandfarms" in url_lower:
            return "uklandandfarms"
        elif "grantco" in url_lower:
            return "grantco"
        elif "sunderlands" in url_lower:
            return "sunderlands"
        elif "primelocation" in url_lower:
            return "primelocation"
        elif "rightmove" in url_lower:
            return "rightmove"
        elif "zoopla" in url_lower:
            return "zoopla"
        elif "onthemarket" in url_lower:
            return "onthemarket"
        return "other"

    def _extract_location(self, text: str) -> str:
        patterns = [
            r"(?:in|at|near)\s+([A-Z][a-zA-Z\s\-]+,\s*(?:Herefordshire|Worcestershire))",
            r"([A-Z][a-zA-Z\s\-]+,\s*(?:Hereford|Worcester|Ross-on-Wye|Leominster|Ledbury|Malvern))",
            r"\b(HR\d+\s+\d[A-Z]{2})\b",
            r"\b(WR\d+\s+\d[A-Z]{2})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return self.clean_text(match.group(1))
        return ""
