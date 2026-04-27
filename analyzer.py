"""
Property analyzer.

Two-stage classification:
  1. Heuristic regex on title + description (free, fast).
  2. Claude API on the full listing page text if the heuristic can't decide.

For each property determines:
  - property_type: one of "detached", "semi-detached", "terrace", "other", "unknown"
  - acres: float or None

Non-detached results trigger auto-dismiss (only on first analysis).
"""
import json
import logging
import os
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from bs4 import BeautifulSoup

import database
from parsers.base import BaseParser

logger = logging.getLogger("property-finder.analyzer")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NON_DETACHED_TYPES = {"semi-detached", "terrace", "other"}

SYSTEM_PROMPT = """You analyse UK property listings.

Given the listing text, return ONLY a single JSON object with these fields:
- "property_type": one of "detached", "semi-detached", "terrace", "other", "unknown"
  - "detached": standalone house (including bungalows, cottages, farmhouses, barn conversions if standalone)
  - "semi-detached": semi-detached house
  - "terrace": terraced house, end-of-terrace, mews
  - "other": flat, apartment, maisonette, retirement, mobile home, shared
  - "unknown": cannot determine from the text
- "acres": number (acres of land/plot) or null if not stated
- "has_land": boolean — true if it has meaningful outdoor land beyond a small garden

Output ONLY the JSON, no prose, no markdown fences. Example:
{"property_type": "detached", "acres": 5.2, "has_land": true}
"""


def classify_type_heuristic(text: str) -> str | None:
    """
    Quick keyword classification. Returns property_type or None if undetermined.
    Order matters — semi-detached must be checked before detached.
    """
    if not text:
        return None
    t = text.lower()

    if re.search(r"\bsemi[\s\-]?detached\b", t):
        return "semi-detached"
    if re.search(r"\b(?:end[\s\-]?of[\s\-]?)?terrac(?:ed|e)\b", t):
        return "terrace"
    if re.search(r"\bmews\b", t):
        return "terrace"
    if re.search(r"\b(?:flat|apartment|maisonette|studio)\b", t):
        return "other"
    if re.search(r"\b(?:retirement|sheltered|park\s+home|mobile\s+home)\b", t):
        return "other"
    if re.search(r"\bdetached\b", t):
        return "detached"
    return None


def fetch_listing_text(url: str, max_chars: int = 8000) -> str:
    """Fetch a listing page and return cleaned text content."""
    if not url:
        return ""
    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", _USER_AGENT)
        req.add_header("Accept", "text/html,application/xhtml+xml")
        with urlopen(req, timeout=15) as resp:
            html = resp.read(500_000).decode("utf-8", errors="ignore")
    except (HTTPError, URLError, OSError) as e:
        logger.debug(f"Failed to fetch listing {url}: {e}")
        return ""

    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars]
    except Exception as e:
        logger.debug(f"Failed to extract text from {url}: {e}")
        return ""


def _add_step(steps: list, label: str, detail: str = "", data: str = "", level: str = "info"):
    """Append a trace step. `data` is shown collapsed in the UI."""
    steps.append({"label": label, "detail": detail, "data": data, "level": level})


def analyze_with_ai(listing_text: str, steps: list | None = None) -> dict | None:
    """
    Call Claude to classify a listing. Returns dict with keys
    'property_type' and 'acres' (acres may be None), or None on error.
    Appends trace info to `steps` if provided.
    """
    s = steps if steps is not None else []

    if not ANTHROPIC_API_KEY:
        _add_step(s, "AI skipped", "ANTHROPIC_API_KEY not set", level="warn")
        logger.debug("ANTHROPIC_API_KEY not set — skipping AI analysis")
        return None
    if not listing_text:
        _add_step(s, "AI skipped", "No listing text to send", level="warn")
        return None

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 200,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": listing_text}],
    }
    _add_step(
        s,
        f"Sending to Claude ({ANTHROPIC_MODEL})",
        f"system={len(SYSTEM_PROMPT)} chars · user={len(listing_text)} chars",
        data=SYSTEM_PROMPT + "\n\n--- USER MESSAGE ---\n" + listing_text,
    )

    body = json.dumps(payload).encode("utf-8")
    req = Request(ANTHROPIC_URL, data=body, method="POST")
    req.add_header("x-api-key", ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("content-type", "application/json")

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()
        except Exception:
            pass
        logger.warning(f"Anthropic API error {e.code}: {err_body[:300]}")
        _add_step(s, "Claude API error", f"HTTP {e.code}", data=err_body[:1000], level="error")
        return None
    except (URLError, OSError) as e:
        logger.warning(f"Anthropic API request failed: {e}")
        _add_step(s, "Claude API failed", str(e), level="error")
        return None

    try:
        text = data["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        logger.warning(f"Unexpected Anthropic response shape: {str(data)[:200]}")
        _add_step(s, "Claude unexpected response", "could not extract text", data=str(data)[:1500], level="error")
        return None

    usage = data.get("usage", {})
    _add_step(
        s,
        "Claude response",
        f"in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')} tokens",
        data=text,
    )

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"AI did not return valid JSON: {text[:200]}")
        _add_step(s, "AI JSON parse failed", "Response was not valid JSON", data=text[:1000], level="error")
        return None

    pt = result.get("property_type")
    if pt not in {"detached", "semi-detached", "terrace", "other", "unknown"}:
        pt = "unknown"

    acres_val = result.get("acres")
    if isinstance(acres_val, (int, float)) and acres_val > 0:
        acres = float(acres_val)
    else:
        acres = None

    _add_step(
        s,
        "AI parsed",
        f"property_type={pt} · acres={acres} · has_land={result.get('has_land')}",
    )

    return {"property_type": pt, "acres": acres}


def analyze_property(prop: dict, steps: list | None = None) -> dict:
    """
    Run heuristic, then AI if needed. Returns dict with:
      - property_type, acres, method ('heuristic'|'ai'|'none')
    Appends trace steps to `steps` if provided.
    """
    s = steps if steps is not None else []

    title = prop.get("title") or ""
    description = prop.get("description") or ""
    combined = f"{title} {description}".strip()

    _add_step(
        s, "Heuristic input",
        f"title + description = {len(combined)} chars",
        data=combined,
    )

    h_type = classify_type_heuristic(combined)
    _add_step(s, "Heuristic property type", h_type or "no match")

    existing_acres = prop.get("acres")
    h_acres = BaseParser.extract_acres(combined) if not existing_acres else existing_acres
    _add_step(
        s, "Heuristic acres",
        f"{h_acres}" if h_acres is not None else "no match"
        + ("" if not existing_acres else " (already in DB)"),
    )

    if h_type and h_acres is not None:
        _add_step(s, "Decision", "Heuristic complete — skipping AI")
        return {"property_type": h_type, "acres": h_acres, "method": "heuristic"}

    if not prop.get("url"):
        _add_step(s, "Decision", "No URL — cannot fetch for AI analysis", level="warn")
        return {"property_type": h_type or "unknown", "acres": h_acres, "method": "heuristic"}

    _add_step(s, "Decision", "Heuristic incomplete — falling back to AI")
    _add_step(s, "Fetching listing page", prop["url"])

    listing_text = fetch_listing_text(prop["url"])
    if listing_text:
        _add_step(
            s, "Fetched listing text",
            f"{len(listing_text)} chars",
            data=listing_text,
        )
    else:
        _add_step(s, "Fetch failed", "Could not retrieve listing page", level="warn")

    ai_result = analyze_with_ai(listing_text, steps=s) if listing_text else None

    if ai_result:
        final = {
            "property_type": ai_result["property_type"] or h_type or "unknown",
            "acres": ai_result["acres"] if ai_result["acres"] is not None else h_acres,
            "method": "ai",
        }
        _add_step(
            s, "Final classification",
            f"{final['property_type']} · acres={final['acres']} (via AI)",
        )
        return final

    final = {
        "property_type": h_type or "unknown",
        "acres": h_acres,
        "method": "heuristic",
    }
    _add_step(
        s, "Final classification",
        f"{final['property_type']} · acres={final['acres']} (AI unavailable, fell back to heuristic)",
        level="warn",
    )
    return final


def analyze_property_by_id(property_id: int, with_trace: bool = False):
    """
    Manually re-analyze a single property. Does NOT auto-dismiss
    (user explicitly asked, so respect their attention).

    If with_trace=True, returns (result, steps); otherwise returns result.
    Returns None / (None, []) if property not found.
    """
    prop = database.get_property(property_id)
    if not prop:
        return (None, []) if with_trace else None

    steps: list = []
    result = analyze_property(prop, steps=steps)
    database.update_analysis(
        property_id=property_id,
        property_type=result["property_type"],
        acres=result["acres"],
        method=result["method"],
        auto_dismiss=False,
    )
    logger.info(
        f"Manual analysis of property {property_id}: "
        f"{result['property_type']} (acres={result['acres']}, method={result['method']})"
    )
    return (result, steps) if with_trace else result


def analyze_new_properties(limit: int = 50):
    """Analyze any properties that haven't been analyzed yet."""
    props = database.get_unanalyzed_properties(limit=limit)
    if not props:
        return

    logger.info(f"Analyzing {len(props)} new properties...")

    dismissed_count = 0
    for prop in props:
        try:
            result = analyze_property(prop)
        except Exception as e:
            logger.warning(f"  Analysis failed for property {prop['id']}: {e}")
            continue

        auto_dismiss = result["property_type"] in NON_DETACHED_TYPES
        database.update_analysis(
            property_id=prop["id"],
            property_type=result["property_type"],
            acres=result["acres"],
            method=result["method"],
            auto_dismiss=auto_dismiss,
        )
        if auto_dismiss:
            dismissed_count += 1
        logger.info(
            f"  Property {prop['id']}: {result['property_type']} "
            f"(acres={result['acres']}, method={result['method']})"
            f"{' — auto-dismissed' if auto_dismiss else ''}"
        )

    logger.info(
        f"Analysis complete: {len(props)} processed, {dismissed_count} auto-dismissed"
    )
