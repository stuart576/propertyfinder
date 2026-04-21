"""Flask web dashboard for Property Finder."""
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for, Response

import config
import database
from email_monitor import check_emails

logger = logging.getLogger("property-finder.web")

app = Flask(__name__)


@app.route("/")
def index():
    sort_by = request.args.get("sort", "first_seen")
    sort_dir = request.args.get("dir", "DESC")
    show_dismissed = request.args.get("dismissed", "0") == "1"
    starred_only = request.args.get("starred", "0") == "1"
    page = max(1, int(request.args.get("page", "1")))
    per_page = 24

    # User filters
    min_beds = _int_or_none(request.args.get("min_beds"))
    max_beds = _int_or_none(request.args.get("max_beds"))
    min_price = _int_or_none(request.args.get("min_price"))
    max_price = _int_or_none(request.args.get("max_price"))
    keyword = request.args.get("q", "").strip()

    filter_kwargs = dict(
        min_beds=min_beds, max_beds=max_beds,
        min_price=min_price, max_price=max_price,
        keyword=keyword,
    )

    properties = database.get_properties(
        show_dismissed=show_dismissed,
        starred_only=starred_only,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=per_page,
        offset=(page - 1) * per_page,
        **filter_kwargs,
    )
    total = database.get_property_count(
        show_dismissed=show_dismissed,
        starred_only=starred_only,
        **filter_kwargs,
    )
    stats = database.get_stats()

    return render_template(
        "dashboard.html",
        properties=properties,
        stats=stats,
        filters=config.FILTERS,
        sort_by=sort_by,
        sort_dir=sort_dir,
        show_dismissed=show_dismissed,
        starred_only=starred_only,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=(total + per_page - 1) // per_page,
        # Active filters for the form
        f_min_beds=min_beds,
        f_max_beds=max_beds,
        f_min_price=min_price,
        f_max_price=max_price,
        f_keyword=keyword,
    )


def _int_or_none(val):
    """Parse an int from a query param, returning None if empty/invalid."""
    if not val:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


@app.route("/api/toggle/<int:property_id>/<field>", methods=["POST"])
def toggle(property_id, field):
    success = database.toggle_property(property_id, field)
    return jsonify({"ok": success})


@app.route("/api/notes/<int:property_id>", methods=["POST"])
def save_notes(property_id):
    data = request.get_json()
    notes = data.get("notes", "")
    database.update_notes(property_id, notes)
    return jsonify({"ok": True})


@app.route("/api/check-now", methods=["POST"])
def check_now():
    """Trigger an immediate email check."""
    stats = check_emails()
    return jsonify(stats)


@app.route("/emails")
def emails():
    page = max(1, int(request.args.get("page", "1")))
    per_page = 50
    email_list = database.get_emails(limit=per_page, offset=(page - 1) * per_page)
    total = database.get_email_count()
    return render_template(
        "emails.html",
        emails=email_list,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=(total + per_page - 1) // per_page,
    )


@app.route("/emails/<int:email_id>")
def email_detail(email_id):
    body_html = database.get_email_body(email_id)
    if body_html is None:
        return "Email not found", 404
    return render_template("email_detail.html", email_id=email_id, body_html=body_html)


@app.route("/emails/<int:email_id>/raw")
def email_raw(email_id):
    """Serve the raw email HTML in an iframe-friendly way."""
    body_html = database.get_email_body(email_id)
    if body_html is None:
        return "Email not found", 404
    return Response(body_html, mimetype="text/html")


@app.route("/map")
def map_view():
    properties = database.get_properties_for_map(show_dismissed=False)
    stats = database.get_stats()
    return render_template("map.html", properties=properties, stats=stats, filters=config.FILTERS)


@app.route("/api/map-data")
def map_data():
    show_dismissed = request.args.get("dismissed", "0") == "1"
    starred_only = request.args.get("starred", "0") == "1"
    properties = database.get_properties_for_map(
        show_dismissed=show_dismissed, starred_only=starred_only,
    )
    markers = []
    for p in properties:
        markers.append({
            "id": p["id"],
            "lat": p["latitude"],
            "lng": p["longitude"],
            "title": p["title"] or "Untitled",
            "price": p["price"],
            "bedrooms": p["bedrooms"],
            "acres": p["acres"],
            "location": p["location"] or p.get("postcode", ""),
            "url": p["url"],
            "image_url": p["image_url"] if p.get("image_url") not in ("", "none", None) else "",
            "source": p["source"],
            "starred": bool(p["starred"]),
        })
    return jsonify(markers)


@app.route("/api/geocode", methods=["POST"])
def trigger_geocode():
    from geocoder import geocode_properties, backfill_postcodes, geocode_all_unmatched
    backfill_postcodes()
    geocode_properties()
    geocode_all_unmatched()
    return jsonify({"ok": True})


@app.route("/api/postcode/<int:property_id>", methods=["POST"])
def set_postcode(property_id):
    """Manually set a postcode for a property and geocode it."""
    from geocoder import bulk_lookup_postcodes
    from parsers.base import BaseParser

    data = request.get_json() or {}
    raw = (data.get("postcode", "") or "").strip()
    if not raw:
        # Empty = clear postcode and geocode
        database.update_postcode(property_id, "")
        database.update_geocode(property_id, None, None)
        return jsonify({"ok": True, "postcode": "", "geocoded": False})

    # Normalise via regex (also validates format)
    normalised = BaseParser.extract_postcode(raw)
    if not normalised:
        return jsonify({"ok": False, "error": "Invalid UK postcode"}), 400

    database.update_postcode(property_id, normalised)
    # Geocode immediately
    results = bulk_lookup_postcodes([normalised])
    coords = results.get(normalised)
    if coords:
        database.update_geocode(property_id, coords["latitude"], coords["longitude"])
        return jsonify({"ok": True, "postcode": normalised, "geocoded": True})
    return jsonify({"ok": True, "postcode": normalised, "geocoded": False})


@app.route("/api/sync-uklaf", methods=["POST"])
def sync_uklaf_route():
    """Scrape UKLAF listings and upsert into the database."""
    from uklaf_scraper import sync_uklaf
    from geocoder import geocode_properties, backfill_postcodes, geocode_all_unmatched
    stats = sync_uklaf()
    backfill_postcodes()
    geocode_properties()
    geocode_all_unmatched()
    return jsonify(stats)


@app.route("/api/reprocess", methods=["POST"])
def reprocess():
    """Clear email log and reset images, then re-check all emails."""
    from geocoder import geocode_properties, backfill_postcodes, geocode_all_unmatched
    database.clear_email_log()
    database.reset_images()
    database.reset_geocodes()
    stats = check_emails()
    backfill_postcodes()
    geocode_properties()
    geocode_all_unmatched()
    return jsonify(stats)


@app.route("/api/stats")
def api_stats():
    return jsonify(database.get_stats())


def run_web():
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False)
