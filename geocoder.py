"""Geocode UK postcodes using postcodes.io (free, no API key)."""
import json
import logging
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import database
from parsers.base import BaseParser

logger = logging.getLogger("property-finder.geocoder")

POSTCODES_IO_URL = "https://api.postcodes.io"


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
