
import json
import os
import logging
from typing import Set

logger = logging.getLogger(__name__)

SIGNAL_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'signals.json')

def load_seen_signals() -> Set[str]:
    """Load seen signals from JSON file."""
    if not os.path.exists(SIGNAL_DB_PATH):
        return set()
    try:
        with open(SIGNAL_DB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # data should be list of strings
            return set(data)
    except Exception as e:
        logger.error(f"Failed to load signals: {e}")
        return set()

def save_seen_signals(signals: Set[str]):
    """Save seen signals to JSON file."""
    try:
        os.makedirs(os.path.dirname(SIGNAL_DB_PATH), exist_ok=True)
        with open(SIGNAL_DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(signals), f)
    except Exception as e:
        logger.error(f"Failed to save signals: {e}")

