"""Property alert email parsers."""
from parsers.base import BaseParser
from parsers.rightmove import RightmoveParser
from parsers.zoopla import ZooplaParser
from parsers.onthemarket import OnTheMarketParser
from parsers.generic import GenericParser

# Registry of parsers keyed by source identifier
PARSERS: dict[str, BaseParser] = {
    "rightmove": RightmoveParser(),
    "zoopla": ZooplaParser(),
    "onthemarket": OnTheMarketParser(),
    "generic": GenericParser(),
}


def get_parser_for_sender(sender_email: str) -> tuple[str, BaseParser]:
    """Match a sender email to the appropriate parser."""
    sender = sender_email.lower()
    if "rightmove" in sender:
        return "rightmove", PARSERS["rightmove"]
    elif "zoopla" in sender:
        return "zoopla", PARSERS["zoopla"]
    elif "onthemarket" in sender:
        return "onthemarket", PARSERS["onthemarket"]
    elif "smallholding" in sender or "uklandandfarms" in sender:
        return "generic", PARSERS["generic"]
    return "generic", PARSERS["generic"]
