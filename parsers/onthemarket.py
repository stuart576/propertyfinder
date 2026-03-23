"""Parser for OnTheMarket property alert emails."""
import re
from bs4 import BeautifulSoup
from parsers.base import BaseParser


class OnTheMarketParser(BaseParser):
    source_name = "onthemarket"

    def parse(self, html: str, text: str = "") -> list[dict]:
        properties = []
        soup = BeautifulSoup(html, "html.parser")

        links = soup.find_all("a", href=re.compile(r"onthemarket\.com.*(?:details|property)", re.IGNORECASE))

        seen_urls = set()
        for link in links:
            url = link.get("href", "").strip()
            clean_url = re.sub(r"[?&]utm_[^&]*", "", url).rstrip("?&")
            if not clean_url or clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            container = link
            for _ in range(8):
                if container.parent:
                    container = container.parent
                    container_text = container.get_text(separator=" ", strip=True)
                    if len(container_text) > 50:
                        break

            full_text = container.get_text(separator=" ", strip=True) if container else ""

            img = container.find("img") if container else None
            image_url = img.get("src", "") or img.get("data-src", "") if img else ""

            price = self.extract_price(full_text)
            bedrooms = self.extract_bedrooms(full_text)
            acres = self.extract_acres(full_text)
            county = self.detect_county(full_text)

            title_text = link.get_text(strip=True)
            if not title_text or len(title_text) < 10:
                title_text = self.clean_text(full_text[:200])

            prop = {
                "source": self.source_name,
                "title": self.clean_text(title_text),
                "price": price,
                "bedrooms": bedrooms,
                "acres": acres,
                "location": self._extract_location(full_text),
                "county": county,
                "url": clean_url,
                "image_url": image_url,
                "description": self.clean_text(full_text[:500]),
            }

            if self.passes_filters(prop):
                properties.append(prop)

        if not properties and text:
            properties = self._parse_text_fallback(text)

        return properties

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

    def _parse_text_fallback(self, text: str) -> list[dict]:
        properties = []
        chunks = re.split(r"(?=£[\d,]+)", text)
        for chunk in chunks:
            if len(chunk) < 30:
                continue
            price = self.extract_price(chunk)
            if not price:
                continue
            url_match = re.search(r"(https?://[^\s]+onthemarket[^\s]+)", chunk)
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
