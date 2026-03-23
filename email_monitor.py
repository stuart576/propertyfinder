"""Microsoft Graph API email monitor for property alert emails."""
import logging
import time
import json
import base64
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

import config
import database
from parsers import get_parser_for_sender

logger = logging.getLogger("property-finder.email")

# Token cache
_token_cache: dict = {"access_token": None, "expires_at": 0}


def get_access_token() -> str:
    """
    Obtain an access token using OAuth2 client credentials flow.
    Caches the token and refreshes when expired.
    """
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["access_token"]

    token_url = f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}/oauth2/v2.0/token"

    data = urlencode({
        "client_id": config.GRAPH_CLIENT_ID,
        "client_secret": config.GRAPH_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode("utf-8")

    req = Request(token_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urlopen(req) as response:
            result = json.loads(response.read().decode())
            _token_cache["access_token"] = result["access_token"]
            _token_cache["expires_at"] = now + result.get("expires_in", 3600)
            logger.info("Obtained new Graph API access token")
            return result["access_token"]
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        logger.error(f"Token request failed ({e.code}): {body}")
        raise


def graph_request(endpoint: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    """Make an authenticated request to the Microsoft Graph API."""
    token = get_access_token()
    url = f"https://graph.microsoft.com/v1.0{endpoint}"

    data = json.dumps(body).encode("utf-8") if body else None
    req = Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        logger.error(f"Graph API error ({e.code}) for {endpoint}: {body_text}")
        raise


def get_messages(top: int = 50, skip: int = 0) -> list[dict]:
    """
    Fetch messages from the shared mailbox.
    Returns newest first, with HTML body content.
    """
    mailbox = config.GRAPH_MAILBOX
    # Select only the fields we need, request HTML body
    select = "id,subject,from,receivedDateTime,body,isRead"
    endpoint = (
        f"/users/{mailbox}/messages"
        f"?$top={top}&$skip={skip}"
        f"&$select={select}"
        f"&$orderby=receivedDateTime%20desc"
        # Only prefer HTML body content
    )

    result = graph_request(endpoint)
    return result.get("value", [])


def get_message_body_html(message_id: str) -> str:
    """
    Fetch the full HTML body of a specific message.
    The list endpoint returns body content, but if it's truncated
    we can fetch individually.
    """
    mailbox = config.GRAPH_MAILBOX
    endpoint = f"/users/{mailbox}/messages/{message_id}?$select=body"
    result = graph_request(endpoint)
    body = result.get("body", {})
    if body.get("contentType") == "html":
        return body.get("content", "")
    return ""


def extract_sender_email(msg: dict) -> str:
    """Extract sender email from a Graph message object."""
    try:
        return msg["from"]["emailAddress"]["address"].lower()
    except (KeyError, TypeError):
        return ""


def extract_plain_text(html: str) -> str:
    """Quick and dirty HTML to text for fallback parsing."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator=" ", strip=True)
    except Exception:
        return ""


def check_emails() -> dict:
    """
    Fetch messages from the M365 shared mailbox via Graph API,
    parse property listings, and store new ones.
    Returns stats dict.
    """
    stats = {"emails_checked": 0, "new_listings": 0, "errors": []}

    if not all([config.GRAPH_TENANT_ID, config.GRAPH_CLIENT_ID,
                config.GRAPH_CLIENT_SECRET, config.GRAPH_MAILBOX]):
        stats["errors"].append("Microsoft Graph credentials not configured")
        logger.error("Microsoft Graph credentials not configured")
        return stats

    try:
        logger.info(f"Fetching messages from {config.GRAPH_MAILBOX}...")
        messages = get_messages(top=50)
        logger.info(f"Retrieved {len(messages)} messages")

        for msg in messages:
            msg_id = msg.get("id", "")
            subject = msg.get("subject", "")
            sender = extract_sender_email(msg)
            received = msg.get("receivedDateTime", "")

            # Use Graph message ID as our unique identifier
            # These are stable and unique per message
            uid = msg_id[:64]  # Truncate — Graph IDs are very long

            # Skip already processed
            if database.is_email_processed(uid):
                continue

            logger.info(f"Processing: {sender} — {subject}")

            # Get HTML body
            body_obj = msg.get("body", {})
            html_body = ""
            if body_obj.get("contentType") == "html":
                html_body = body_obj.get("content", "")

            # If body wasn't included or is empty, fetch it directly
            if not html_body:
                try:
                    html_body = get_message_body_html(msg_id)
                except Exception as e:
                    logger.warning(f"Could not fetch message body: {e}")

            text_body = extract_plain_text(html_body)

            # Get the appropriate parser
            source_name, parser = get_parser_for_sender(sender)

            # Parse listings
            try:
                listings = parser.parse(html_body, text_body)
            except Exception as e:
                logger.error(f"Parser error for {source_name}: {e}")
                listings = []
                stats["errors"].append(f"Parser error ({source_name}): {str(e)}")

            # Store listings
            new_count = 0
            for listing in listings:
                is_new, _ = database.upsert_property(listing)
                if is_new:
                    new_count += 1

            stats["new_listings"] += new_count
            stats["emails_checked"] += 1

            # Log the processed email
            database.log_email(
                uid=uid,
                sender=sender,
                subject=subject,
                received_at=received,
                listings_found=len(listings),
                body_html=html_body,
            )

            logger.info(f"  → Found {len(listings)} listings ({new_count} new)")

    except HTTPError as e:
        err = f"Graph API error: {e.code}"
        logger.error(err)
        stats["errors"].append(err)
    except Exception as e:
        err = f"Email check failed: {str(e)}"
        logger.error(err)
        stats["errors"].append(err)

    logger.info(
        f"Email check complete: {stats['emails_checked']} emails, "
        f"{stats['new_listings']} new listings"
    )
    return stats


def run_monitor_loop():
    """Run the email monitor in a loop."""
    logger.info(f"Starting email monitor (interval: {config.CHECK_INTERVAL}s)")
    while True:
        try:
            check_emails()
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        time.sleep(config.CHECK_INTERVAL)
