"""Configuration for Property Finder."""
import os

# Microsoft Graph API settings (M365 shared mailbox)
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
GRAPH_MAILBOX = os.getenv("GRAPH_MAILBOX", "")  # e.g. property-alerts@yourdomain.co.uk

# Check interval (seconds)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))  # Default: 1 hour

# Database
DB_PATH = os.getenv("DB_PATH", "/data/properties.db")

# Web dashboard
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8585"))

# Filter criteria
FILTERS = {
    "max_price": int(os.getenv("MAX_PRICE", "850000")),
    "min_bedrooms": int(os.getenv("MIN_BEDROOMS", "3")),
    "min_acres": float(os.getenv("MIN_ACRES", "2.0")),
    "property_type": "detached",
    "counties": ["herefordshire", "worcestershire"],
}
