"""SQLite database for property listings storage and deduplication."""
import sqlite3
import hashlib
import os
from datetime import datetime
from typing import Optional

import config


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            title TEXT,
            price INTEGER,
            bedrooms INTEGER,
            acres REAL,
            location TEXT,
            county TEXT,
            url TEXT,
            image_url TEXT,
            description TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dismissed INTEGER DEFAULT 0,
            starred INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_uid TEXT UNIQUE NOT NULL,
            sender TEXT,
            subject TEXT,
            received_at TIMESTAMP,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            listings_found INTEGER DEFAULT 0,
            body_html TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_properties_price ON properties(price);
        CREATE INDEX IF NOT EXISTS idx_properties_county ON properties(county);
        CREATE INDEX IF NOT EXISTS idx_properties_dismissed ON properties(dismissed);
        CREATE INDEX IF NOT EXISTS idx_properties_first_seen ON properties(first_seen);
    """)
    conn.commit()

    # Migrate: add body_html column if missing (existing databases)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(email_log)").fetchall()]
    if "body_html" not in cols:
        conn.execute("ALTER TABLE email_log ADD COLUMN body_html TEXT DEFAULT ''")
        conn.commit()

    # Migrate: add geocoding columns if missing
    prop_cols = [row[1] for row in conn.execute("PRAGMA table_info(properties)").fetchall()]
    if "postcode" not in prop_cols:
        conn.execute("ALTER TABLE properties ADD COLUMN postcode TEXT DEFAULT ''")
        conn.commit()
    if "latitude" not in prop_cols:
        conn.execute("ALTER TABLE properties ADD COLUMN latitude REAL")
        conn.commit()
    if "longitude" not in prop_cols:
        conn.execute("ALTER TABLE properties ADD COLUMN longitude REAL")
        conn.commit()

    conn.close()


def make_fingerprint(url: str, title: str, price: Optional[int]) -> str:
    """Create a dedup fingerprint from listing details."""
    # Prefer URL-based fingerprint if we have a clean listing URL
    if url and ("rightmove" in url or "zoopla" in url or "onthemarket" in url):
        # Extract property ID from URL where possible
        raw = url.strip().lower()
    else:
        # Fall back to title + price combo
        raw = f"{(title or '').strip().lower()}|{price or 0}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def upsert_property(data: dict) -> tuple[bool, int]:
    """
    Insert or update a property listing.
    Returns (is_new, property_id).
    """
    fp = make_fingerprint(data.get("url", ""), data.get("title", ""), data.get("price"))
    conn = get_connection()
    cursor = conn.cursor()

    existing = cursor.execute(
        "SELECT id FROM properties WHERE fingerprint = ?", (fp,)
    ).fetchone()

    if existing:
        # Update last_seen, and refresh image/postcode if we now have better data
        new_image = data.get("image_url", "")
        new_postcode = data.get("postcode", "")
        cursor.execute(
            """UPDATE properties SET last_seen = ?,
               image_url = CASE WHEN (image_url IN ('', 'none') OR image_url IS NULL)
                                 AND ? != '' THEN ? ELSE image_url END,
               postcode = CASE WHEN (postcode IS NULL OR postcode = '')
                                AND ? != '' THEN ? ELSE postcode END
               WHERE fingerprint = ?""",
            (datetime.utcnow().isoformat(),
             new_image, new_image,
             new_postcode, new_postcode,
             fp),
        )
        conn.commit()
        pid = existing["id"]
        conn.close()
        return False, pid

    cursor.execute(
        """INSERT INTO properties
           (fingerprint, source, title, price, bedrooms, acres, location, county, url, image_url, description, postcode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fp,
            data.get("source", "unknown"),
            data.get("title", ""),
            data.get("price"),
            data.get("bedrooms"),
            data.get("acres"),
            data.get("location", ""),
            data.get("county", ""),
            data.get("url", ""),
            data.get("image_url", ""),
            data.get("description", ""),
            data.get("postcode", ""),
        ),
    )
    conn.commit()
    pid = cursor.lastrowid
    conn.close()
    return True, pid


def log_email(uid: str, sender: str, subject: str, received_at: str, listings_found: int, body_html: str = ""):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO email_log (email_uid, sender, subject, received_at, listings_found, body_html)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, sender, subject, received_at, listings_found, body_html),
        )
        conn.commit()
    finally:
        conn.close()


def is_email_processed(uid: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM email_log WHERE email_uid = ?", (uid,)).fetchone()
    conn.close()
    return row is not None


def _build_filter_clauses(
    show_dismissed: bool = False,
    starred_only: bool = False,
    min_beds: Optional[int] = None,
    max_beds: Optional[int] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    keyword: str = "",
) -> tuple[str, list]:
    """Build WHERE clause and params from filter arguments."""
    conditions = []
    params = []
    if not show_dismissed:
        conditions.append("dismissed = 0")
    if starred_only:
        conditions.append("starred = 1")
    if min_beds is not None:
        conditions.append("bedrooms >= ?")
        params.append(min_beds)
    if max_beds is not None:
        conditions.append("bedrooms <= ?")
        params.append(max_beds)
    if min_price is not None:
        conditions.append("price >= ?")
        params.append(min_price)
    if max_price is not None:
        conditions.append("price <= ?")
        params.append(max_price)
    if keyword:
        conditions.append("(title LIKE ? OR location LIKE ? OR description LIKE ? OR county LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def get_properties(
    show_dismissed: bool = False,
    starred_only: bool = False,
    sort_by: str = "first_seen",
    sort_dir: str = "DESC",
    limit: int = 100,
    offset: int = 0,
    min_beds: Optional[int] = None,
    max_beds: Optional[int] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    keyword: str = "",
) -> list[dict]:
    conn = get_connection()
    where, params = _build_filter_clauses(
        show_dismissed, starred_only, min_beds, max_beds, min_price, max_price, keyword,
    )

    allowed_sorts = {"first_seen", "price", "acres", "bedrooms", "last_seen"}
    sort_col = sort_by if sort_by in allowed_sorts else "first_seen"
    direction = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    rows = conn.execute(
        f"""SELECT * FROM properties {where}
            ORDER BY {sort_col} {direction}
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_property_count(
    show_dismissed: bool = False,
    starred_only: bool = False,
    min_beds: Optional[int] = None,
    max_beds: Optional[int] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    keyword: str = "",
) -> int:
    conn = get_connection()
    where, params = _build_filter_clauses(
        show_dismissed, starred_only, min_beds, max_beds, min_price, max_price, keyword,
    )
    row = conn.execute(f"SELECT COUNT(*) as cnt FROM properties {where}", params).fetchone()
    conn.close()
    return row["cnt"]


def get_stats() -> dict:
    conn = get_connection()
    stats = {}
    stats["total"] = conn.execute("SELECT COUNT(*) as c FROM properties").fetchone()["c"]
    stats["active"] = conn.execute("SELECT COUNT(*) as c FROM properties WHERE dismissed = 0").fetchone()["c"]
    stats["starred"] = conn.execute("SELECT COUNT(*) as c FROM properties WHERE starred = 1").fetchone()["c"]
    stats["emails_processed"] = conn.execute("SELECT COUNT(*) as c FROM email_log").fetchone()["c"]

    row = conn.execute(
        "SELECT first_seen FROM properties ORDER BY first_seen DESC LIMIT 1"
    ).fetchone()
    stats["last_listing"] = row["first_seen"] if row else None

    row = conn.execute(
        "SELECT processed_at FROM email_log ORDER BY processed_at DESC LIMIT 1"
    ).fetchone()
    stats["last_check"] = row["processed_at"] if row else None

    conn.close()
    return stats


def toggle_property(property_id: int, field: str) -> bool:
    if field not in ("dismissed", "starred"):
        return False
    conn = get_connection()
    conn.execute(
        f"UPDATE properties SET {field} = CASE WHEN {field} = 0 THEN 1 ELSE 0 END WHERE id = ?",
        (property_id,),
    )
    conn.commit()
    conn.close()
    return True


def get_emails(limit: int = 50, offset: int = 0) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, email_uid, sender, subject, received_at, processed_at, listings_found
           FROM email_log ORDER BY processed_at DESC LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_email_count() -> int:
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM email_log").fetchone()
    conn.close()
    return row["cnt"]


def get_email_body(email_id: int) -> Optional[str]:
    conn = get_connection()
    row = conn.execute("SELECT body_html FROM email_log WHERE id = ?", (email_id,)).fetchone()
    conn.close()
    if row:
        return row["body_html"]
    return None


def get_properties_missing_images(limit: int = 20) -> list[dict]:
    """Get properties that have a URL but no image."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, url FROM properties
           WHERE url != '' AND (image_url IS NULL OR image_url = '')
           AND dismissed = 0
           ORDER BY first_seen DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_image_url(property_id: int, image_url: str):
    conn = get_connection()
    conn.execute("UPDATE properties SET image_url = ? WHERE id = ?", (image_url, property_id))
    conn.commit()
    conn.close()


def clear_email_log():
    """Delete all email log entries so emails get reprocessed on next check."""
    conn = get_connection()
    conn.execute("DELETE FROM email_log")
    conn.commit()
    conn.close()


def reset_images():
    """Clear all property images so they get re-fetched."""
    conn = get_connection()
    conn.execute("UPDATE properties SET image_url = '' WHERE dismissed = 0")
    conn.commit()
    conn.close()


def get_properties_missing_geocode(limit: int = 100) -> list[dict]:
    """Get properties that have a postcode but no lat/lng."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, postcode FROM properties
           WHERE postcode != '' AND postcode IS NOT NULL
           AND (latitude IS NULL OR longitude IS NULL)
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_geocode(property_id: int, latitude: float, longitude: float):
    conn = get_connection()
    conn.execute(
        "UPDATE properties SET latitude = ?, longitude = ? WHERE id = ?",
        (latitude, longitude, property_id),
    )
    conn.commit()
    conn.close()


def update_postcode(property_id: int, postcode: str):
    conn = get_connection()
    conn.execute(
        "UPDATE properties SET postcode = ? WHERE id = ?",
        (postcode, property_id),
    )
    conn.commit()
    conn.close()


def get_properties_for_map(
    show_dismissed: bool = False,
    starred_only: bool = False,
    min_beds: Optional[int] = None,
    max_beds: Optional[int] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    keyword: str = "",
) -> list[dict]:
    """Get properties that have lat/lng for map display."""
    where, params = _build_filter_clauses(
        show_dismissed, starred_only, min_beds, max_beds, min_price, max_price, keyword,
    )
    geo_clause = "latitude IS NOT NULL AND longitude IS NOT NULL"
    if where:
        where += f" AND {geo_clause}"
    else:
        where = f"WHERE {geo_clause}"
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM properties {where}", params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_geocodes():
    """Clear all geocode data so properties get re-geocoded."""
    conn = get_connection()
    conn.execute("UPDATE properties SET latitude = NULL, longitude = NULL")
    conn.commit()
    conn.close()


def update_notes(property_id: int, notes: str):
    conn = get_connection()
    conn.execute("UPDATE properties SET notes = ? WHERE id = ?", (notes, property_id))
    conn.commit()
    conn.close()
