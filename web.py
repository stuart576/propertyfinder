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

    properties = database.get_properties(
        show_dismissed=show_dismissed,
        starred_only=starred_only,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    total = database.get_property_count(show_dismissed=show_dismissed)
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
    )


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


@app.route("/api/reprocess", methods=["POST"])
def reprocess():
    """Clear email log and reset images, then re-check all emails."""
    database.clear_email_log()
    database.reset_images()
    stats = check_emails()
    return jsonify(stats)


@app.route("/api/stats")
def api_stats():
    return jsonify(database.get_stats())


def run_web():
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False)
