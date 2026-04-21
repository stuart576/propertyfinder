"""Geocode UK postcodes via postcodes.io and fallback to Nominatim for locations."""
import json
import logging
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import database
from parsers.base import BaseParser

logger = logging.getLogger("property-finder.geocoder")

POSTCODES_IO_URL = "https://api.postcodes.io"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim policy requires a User-Agent that identifies the application
NOMINATIM_USER_AGENT = "PropertyFinder/1.0 (stuart.parsons1@gmail.com)"

# Nominatim rate limit: max 1 request per second
_last_nominatim_call = 0.0


def bulk_lookup_postcodes(postcodes: list[str]) -> dict[str, dict]:
    """
    Bulk lookup up to 100 postcodes via POST /postcodes.
    Returns {postcode: {"latitude": ..., "longitude": ...}} for successful lookups.
    """
    if not postcodes:
        return {}
    url = f"{POSTCODES_IO_URL}/postcodes"
    payload = json.dumps({"postcodes": postcodes[:100]}).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    results = {}
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == 200:
                for item in data.get("result", []):
                    query_pc = item.get("query", "")
                    result = item.get("result")
                    if result:
                        results[query_pc.upper().strip()] = {
                            "latitude": result["latitude"],
                            "longitude": result["longitude"],
                        }
    except (HTTPError, URLError, OSError) as e:
        logger.error(f"Bulk postcode lookup failed: {e}")
    return results


def geocode_properties():
    """Geocode all properties that have a postcode but no lat/lng. Uses bulk API."""
    missing = database.get_properties_missing_geocode(limit=100)
    if not missing:
        return

    logger.info(f"Geocoding {len(missing)} properties...")

    # Build postcode -> [property_ids] mapping
    pc_to_ids: dict[str, list[int]] = {}
    for prop in missing:
        pc = prop["postcode"].upper().strip()
        pc_to_ids.setdefault(pc, []).append(prop["id"])

    # Bulk lookup (chunks of 100)
    unique_postcodes = list(pc_to_ids.keys())
    for i in range(0, len(unique_postcodes), 100):
        batch = unique_postcodes[i : i + 100]
        results = bulk_lookup_postcodes(batch)

        for pc, coords in results.items():
            for pid in pc_to_ids.get(pc, []):
                database.update_geocode(pid, coords["latitude"], coords["longitude"])

        failed = [pc for pc in batch if pc not in results]
        if failed:
            logger.warning(f"  Could not geocode postcodes: {', '.join(failed)}")

    geocoded = sum(1 for pc in unique_postcodes if pc in results) if len(unique_postcodes) <= 100 else "multiple batches"
    logger.info(f"Geocoding complete")


def backfill_postcodes():
    """Extract postcodes from existing properties that have text fields but no postcode."""
    conn = database.get_connection()
    rows = conn.execute(
        """SELECT id, title, location, description FROM properties
           WHERE (postcode IS NULL OR postcode = '')"""
    ).fetchall()
    conn.close()

    count = 0
    for row in rows:
        pc = BaseParser.extract_postcode_from_fields(
            row["title"] or "", row["location"] or "", row["description"] or ""
        )
        if pc:
            database.update_postcode(row["id"], pc)
            count += 1

    if count:
        logger.info(f"Backfilled postcodes for {count} properties")


def _location_cache_key(location: str, county: str) -> str:
    """Build a normalised cache key from location + county."""
    return f"{(location or '').strip().lower()}|{(county or '').strip().lower()}"


def _nominatim_lookup(location: str, county: str) -> tuple[float, float] | None:
    """
    Query Nominatim for a location. Respects 1 req/sec rate limit.
    Returns (lat, lng) or None on failure.
    """
    global _last_nominatim_call

    # Build query — only include non-empty parts
    parts = [p for p in [location.strip(), county.strip(), "UK"] if p]
    if not parts or parts == ["UK"]:
        return None
    query = ", ".join(parts)

    url = f"{NOMINATIM_URL}?{urlencode({'q': query, 'format': 'json', 'limit': '1'})}"

    # Rate limit: 1 req/sec
    elapsed = time.time() - _last_nominatim_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    req = Request(url, method="GET")
    req.add_header("User-Agent", NOMINATIM_USER_AGENT)
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            _last_nominatim_call = time.time()
            data = json.loads(resp.read().decode())
            if data and isinstance(data, list):
                lat = float(data[0]["lat"])
                lng = float(data[0]["lon"])
                return (lat, lng)
    except (HTTPError, URLError, OSError, KeyError, ValueError) as e:
        _last_nominatim_call = time.time()
        logger.debug(f"Nominatim lookup failed for '{query}': {e}")

    return None


def geocode_location(location: str, county: str) -> tuple[float | None, float | None]:
    """
    Geocode a location via Nominatim, using the cache to avoid repeat queries.
    Returns (lat, lng), each of which may be None if the lookup failed.
    """
    key = _location_cache_key(location, county)
    cached = database.get_cached_geocode(key)
    if cached is not None:
        return cached  # (lat, lng), possibly (None, None)

    coords = _nominatim_lookup(location, county)
    if coords:
        database.cache_geocode(key, coords[0], coords[1])
        return coords

    # Cache the failure so we don't retry
    database.cache_geocode(key, None, None)
    return (None, None)


def geocode_all_unmatched():
    """
    Geocode properties that have no lat/lng and no postcode, using Nominatim
    with the location + county text. Caches results per unique location.
    """
    props = database.get_properties_needing_location_geocode(limit=500)
    if not props:
        return

    logger.info(f"Location-geocoding {len(props)} unmatched properties...")

    matched = 0
    for prop in props:
        lat, lng = geocode_location(prop.get("location") or "", prop.get("county") or "")
        if lat is not None and lng is not None:
            database.update_geocode(prop["id"], lat, lng)
            matched += 1

    logger.info(f"Location-geocoding complete: matched {matched}/{len(props)}")
