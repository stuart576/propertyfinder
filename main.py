"""Property Finder - main entry point."""
import logging
import threading
import sys

import database
import config
from web import run_web
from email_monitor import run_monitor_loop

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("property-finder")


def main():
    logger.info("=" * 50)
    logger.info("Property Finder starting up")
    logger.info(f"  Mailbox: {config.GRAPH_MAILBOX or '(not configured)'}")
    logger.info(f"  Tenant: {config.GRAPH_TENANT_ID[:8]}..." if config.GRAPH_TENANT_ID else "  Tenant: (not configured)")
    logger.info(f"  Check interval: {config.CHECK_INTERVAL}s")
    logger.info(f"  Max price: £{config.FILTERS['max_price']:,}")
    logger.info(f"  Min bedrooms: {config.FILTERS['min_bedrooms']}")
    logger.info(f"  Min acres: {config.FILTERS['min_acres']}")
    logger.info(f"  Counties: {', '.join(config.FILTERS['counties'])}")
    logger.info(f"  Dashboard: http://localhost:{config.WEB_PORT}")
    logger.info("=" * 50)

    # Initialise database
    database.init_db()

    # Start email monitor in background thread
    graph_configured = all([
        config.GRAPH_TENANT_ID, config.GRAPH_CLIENT_ID,
        config.GRAPH_CLIENT_SECRET, config.GRAPH_MAILBOX,
    ])
    if graph_configured:
        monitor_thread = threading.Thread(target=run_monitor_loop, daemon=True)
        monitor_thread.start()
        logger.info("Email monitor thread started (Microsoft Graph)")
    else:
        logger.warning(
            "Microsoft Graph credentials not fully set — running dashboard only. "
            "Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, and GRAPH_MAILBOX."
        )

    # Start web dashboard (blocks)
    run_web()


if __name__ == "__main__":
    main()
