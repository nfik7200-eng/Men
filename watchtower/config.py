"""Runtime configuration (environment variables, no secrets in code)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_URL = os.getenv("WATCHTOWER_DB_URL", f"sqlite:///{ROOT / 'data' / 'watchtower.db'}")

# Honest, identifiable crawler identity. Operators should set a contact URL/email.
CONTACT = os.getenv("WATCHTOWER_CONTACT", "set-WATCHTOWER_CONTACT")
USER_AGENT = f"WatchtowerBot/0.1 (price research; contact: {CONTACT})"

MIN_REQUEST_DELAY_S = float(os.getenv("WATCHTOWER_MIN_DELAY", "1.5"))
HTTP_TIMEOUT_S = float(os.getenv("WATCHTOWER_HTTP_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("WATCHTOWER_MAX_RETRIES", "3"))

# Deal-scoring evidence thresholds (transparent, tunable).
MIN_HISTORY_OBS = 3
MIN_HISTORY_SPAN_DAYS = 7
MIN_PEERS = 5

ADMIN_TOKEN = os.getenv("WATCHTOWER_ADMIN_TOKEN")  # if unset, admin only from loopback

# Optional local LLM (Ollama). Disabled unless OLLAMA_URL is set.
OLLAMA_URL = os.getenv("OLLAMA_URL")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

AFFILIATE_CONFIG = os.getenv("AFFILIATE_CONFIG", str(ROOT / "data" / "affiliate.json"))
IMPORT_RULES = os.getenv("IMPORT_RULES", str(ROOT / "data" / "import_rules.json"))
