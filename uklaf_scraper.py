"""Scraper for uklandandfarms.co.uk rural property listings."""
import logging
import re
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from bs4 import BeautifulSoup

import config
import database
from parsers.base import BaseParser

logger = logging.getLogger("property-finder.uklaf")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SOURCE_NAME = "uklandandfarms"
BASE_URL = "https://www.uklandandfarms.co.uk"

COUNTY_URLS = {
    "herefordshire": f"{BASE_URL}/rural-properties-for-sale/west-midlands/herefordshire/",
    "worcestershire": f"{BASE_URL}/rural-properties-for-sale/west-midlands/worcestershire/",
}

MAX_PAGES = 20
DELAY_SECONDS = 1


def fetch_page(url: str, timeout: int = 15) -> str:
    """Fetch HTML from a URL."""
    req = Request(url, method="GET")
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("Accept", "text/html,application/xhtml+xml")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError, OSError) as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return ""


def _parse_listing_li(li, county: str) -> dict | None:
    """Parse a single <li> property card from the propertyList."""
    # First <a> contains the detail link + thumbnail image
    first_link = li.find("a")
    if not first_link:
        return None

    href = (first_link.get("href") or "").strip()
    if not href or "rural-property-for-sale" not in href:
        return None
    url = urljoin(BASE_URL, href.split("?")[0].rstrip("/"))

    # Image is the <img> inside the first <a>
    image_url = ""
    img = first_link.find("img")
    if img:
        src = img.get("src", "") or img.get("data-src", "")
        if src:
            image_url = urljoin(BASE_URL, src)

    # Skip saved-copy relative paths — only use real http(s) URLs
    if image_url and not image_url.startswith(("http://", "https://")):
        image_url = ""

    # Title / price / acres / location live in the <h3>
    h3 = li.find("h3")
    h3_text = h3.get_text(separator=" ", strip=True) if h3 else ""

    # The h3 anchor text is typically "For Sale - Guide Price £X,XXX,XXX"
    title_link = h3.find("a") if h3 else None
    title_text = title_link.get_text(strip=True) if title_link else h3_text

    # Description from <div class="right"><p>
    desc = ""
    right_div = li.find("div", class_="right")
    if right_div:
        p = right_div.find("p")
        if p:
            desc = BaseParser.clean_text(p.get_text(separator=" ", strip=True))

    # Location is the text after the <br> in the h3, e.g. "809.51 acres, Worcestershire"
    # Strip the "X acres," prefix if present to isolate the place name
    location = ""
    if h3:
        # Find text nodes that follow the <br>
        h3_html = h3.decode_contents()
        if "<br" in h3_html.lower():
            tail = re.split(r"<br\s*/?>", h3_html, maxsplit=1, flags=re.IGNORECASE)[1]
            tail_text = BeautifulSoup(tail, "html.parser").get_text(separator=" ", strip=True)
            # e.g. "809.51 acres, Worcestershire" -> "Worcestershire"
            m = re.search(r"acres?[, ]+(.+)$", tail_text, re.IGNORECASE)
            if m:
                location = BaseParser.clean_text(m.group(1))
            else:
                location = BaseParser.clean_text(tail_text)

    # Combined text for fallback extraction
    combined = " ".join(filter(None, [title_text, h3_text, desc, location]))

    price = BaseParser.extract_price(combined)
    acres = BaseParser.extract_acres(combined)
    postcode = BaseParser.extract_postcode(combined)

    # Title cleanup: drop the "For Sale - " prefix and use location as the headline
    clean_title = re.sub(r"^\s*For Sale\s*-\s*", "", title_text, flags=re.IGNORECASE)
    if location and (not clean_title or len(clean_title) < 10):
        clean_title = location
    if not clean_title:
        clean_title = desc[:100] if desc else "UKLAF property"

    return {
        "source": SOURCE_NAME,
        "title": BaseParser.clean_text(clean_title)[:200],
        "price": price,
        "bedrooms": BaseParser.extract_bedrooms(combined),
        "acres": acres,
        "location": location,
        "county": county,
        "url": url,
        "image_url": image_url,
        "description": desc[:500],
        "postcode": postcode,
    }


def _parse_detail_page(html: str) -> dict:
    """
    Parse a UKLAF detail page. Returns a dict of fields to merge into the
    listing card data (empty fields are skipped by the caller).
    """
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}

    # Main heading: e.g. "23.59 acres, Hereford, Herefordshire, HR5 3RW"
    h1 = soup.find("h1")
    h1_head = ""
    if h1:
        h1_html = h1.decode_contents()
        h1_head = BeautifulSoup(
            re.split(r"<br\s*/?>", h1_html, maxsplit=1, flags=re.IGNORECASE)[0],
            "html.parser",
        ).get_text(separator=" ", strip=True)

    # Postcode: try h1 first, then the Google Maps quicklink (q=HR5 3RW)
    postcode = BaseParser.extract_postcode(h1_head)
    if not postcode:
        maps_link = soup.find("a", href=re.compile(r"maps\.google", re.IGNORECASE))
        if maps_link:
            postcode = BaseParser.extract_postcode(maps_link.get("href", ""))
    if postcode:
        result["postcode"] = postcode

    # Location: strip "NN.NN acres, " prefix from h1 head, and trailing postcode
    if h1_head:
        loc = re.sub(r"^\s*\d+(?:\.\d+)?\s*acres?[, ]*", "", h1_head, flags=re.IGNORECASE)
        if postcode:
            loc = loc.replace(postcode, "")
        loc = BaseParser.clean_text(loc).rstrip(", ").strip()
        if loc:
            result["location"] = loc

    # Best image: prefer the full-size pop_*, then the medium man_*,
    # falling back to any property image in the gallery
    gallery = soup.find("div", id="ImageGallery")
    if gallery:
        best_img = ""
        for a in gallery.find_all("a"):
            img = a.find("img")
            if not img:
                continue
            for key in ("data-big", "href", "src", "data-src"):
                val = a.get(key) if key in ("href",) else img.get(key, "")
                if not val:
                    continue
                if val.startswith("/media/properties/"):
                    best_img = urljoin(BASE_URL, val)
                    break
            if best_img and ("pop_" in best_img or "man_" in best_img):
                break
        if best_img:
            result["image_url"] = best_img

    # Description: the biggest <p class="clearboth"> block
    desc_parts = []
    for p in soup.find_all("p", class_="clearboth"):
        text = p.get_text(separator=" ", strip=True)
        if len(text) > 100:
            desc_parts.append(text)
    if desc_parts:
        full_desc = " ".join(desc_parts)
        result["description"] = BaseParser.clean_text(full_desc)[:4000]

    # Feature bullets: sometimes surface bedrooms
    feature_ul = soup.find("ul", class_="clearboth")
    if feature_ul:
        bullets_text = feature_ul.get_text(separator=" ", strip=True)
        beds = BaseParser.extract_bedrooms(bullets_text)
        if beds:
            result["bedrooms"] = beds

    # Fallback: title from h1 if more specific than the card
    if h1_head:
        result["title"] = BaseParser.clean_text(h1_head)[:200]

    return result


def fetch_detail_page(url: str) -> dict:
    """Fetch a detail page URL and parse it. Respects rate limit via caller."""
    html = fetch_page(url)
    return _parse_detail_page(html)


def enrich_with_detail_page(prop: dict):
    """Fetch the detail page and merge better data into the listing dict in-place."""
    detail = fetch_detail_page(prop["url"])
    for key, value in detail.items():
        if not value:
            continue
        # Always prefer detail-page values for these, otherwise only fill gaps
        if key in ("description", "image_url", "postcode", "location", "title"):
            prop[key] = value
        elif not prop.get(key):
            prop[key] = value


def parse_listings(html: str, county: str) -> list[dict]:
    """Parse a listings page and return a list of property dicts."""
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    listings = []
    seen_urls = set()

    # All property cards live inside <div id="propertyList"><ul><li>...</li></ul></div>
    property_list = soup.find("div", id="propertyList")
    if not property_list:
        return []

    for li in property_list.find_all("li"):
        prop = _parse_listing_li(li, county)
        if not prop or not prop["url"]:
            continue
        if prop["url"] in seen_urls:
            continue
        seen_urls.add(prop["url"])
        listings.append(prop)

    return listings


def passes_filters(prop: dict) -> bool:
    """Apply configured filters: max price, min acres."""
    f = config.FILTERS
    if prop.get("price") and prop["price"] > f["max_price"]:
        return False
    if prop.get("acres") is not None and prop["acres"] < f["min_acres"]:
        return False
    return True


def scrape_county(county: str) -> list[dict]:
    """Scrape all pages for a county. Returns list of property dicts."""
    base_url = COUNTY_URLS.get(county)
    if not base_url:
        return []

    all_props = []
    seen_urls_all = set()

    for page in range(1, MAX_PAGES + 1):
        # UKLAF pagination is /page-N/ (not ?page=N)
        url = base_url if page == 1 else f"{base_url}page-{page}/"
        logger.info(f"Fetching {county} page {page}: {url}")

        html = fetch_page(url)
        if not html:
            break

        page_listings = parse_listings(html, county)
        if not page_listings:
            logger.info(f"  No listings found on page {page} — stopping")
            break

        new_on_page = [p for p in page_listings if p["url"] not in seen_urls_all]
        if not new_on_page:
            logger.info(f"  Page {page} had no new URLs — stopping pagination")
            break

        for p in new_on_page:
            seen_urls_all.add(p["url"])
        all_props.extend(new_on_page)

        logger.info(f"  Found {len(new_on_page)} listings on page {page}")
        time.sleep(DELAY_SECONDS)

    # Enrich each listing by fetching its detail page
    logger.info(f"Enriching {len(all_props)} {county} listings from detail pages...")
    for i, prop in enumerate(all_props, start=1):
        try:
            enrich_with_detail_page(prop)
        except Exception as e:
            logger.warning(f"  Detail fetch failed for {prop['url']}: {e}")
        if i < len(all_props):
            time.sleep(DELAY_SECONDS)

    return all_props


def sync_uklaf() -> dict:
    """
    Scrape UKLAF for all configured counties, upsert into database.
    Returns stats: {"fetched": N, "new": N, "updated": N, "filtered": N}.
    """
    stats = {"fetched": 0, "new": 0, "updated": 0, "filtered": 0}

    for county in COUNTY_URLS:
        props = scrape_county(county)
        stats["fetched"] += len(props)

        for prop in props:
            if not passes_filters(prop):
                stats["filtered"] += 1
                continue
            is_new, _ = database.upsert_property(prop)
            if is_new:
                stats["new"] += 1
            else:
                stats["updated"] += 1

    logger.info(
        f"UKLAF sync complete: fetched {stats['fetched']}, "
        f"{stats['new']} new, {stats['updated']} updated, {stats['filtered']} filtered"
    )
    return stats
