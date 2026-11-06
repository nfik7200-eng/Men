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
MIN_OBS_GAP_HOURS = 6      # observations closer than this are one collection (burst/duplicates)
MIN_REFERENCE_DAYS = 3     # the 'usual' price must have held this long to define a drop

ADMIN_TOKEN = os.getenv("WATCHTOWER_ADMIN_TOKEN")  # REQUIRED to use /admin; unset => admin disabled (fail closed)
TRUST_PROXY = os.getenv("WATCHTOWER_TRUST_PROXY", "0") == "1"  # only behind YOUR reverse proxy (uses rightmost X-Forwarded-For)
COOKIE_SECURE = os.getenv("WATCHTOWER_COOKIE_SECURE", "0") == "1"
RATE_LIMIT_PER_MIN = int(os.getenv("WATCHTOWER_RATE_LIMIT", "120"))  # per client IP; 0 disables

# Optional local LLM (Ollama). Disabled unless OLLAMA_URL is set.
OLLAMA_URL = os.getenv("OLLAMA_URL")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

AFFILIATE_CONFIG = os.getenv("AFFILIATE_CONFIG", str(ROOT / "data" / "affiliate.json"))
IMPORT_RULES = os.getenv("IMPORT_RULES", str(ROOT / "data" / "import_rules.json"))
