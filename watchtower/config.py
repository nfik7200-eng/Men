"""Runtime configuration (environment variables, no secrets in code)."""
import os
import re
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

MAX_PAGE_BUDGET = 500  # hard ceiling on product pages per source per run, whatever the environment says


def page_budget(source_key: str, default: int) -> int:
    """Product pages fetched per run for one source: WATCHTOWER_BUDGET_<KEY> if it is a positive integer (capped at
    MAX_PAGE_BUDGET), else the adapter default. An explicit `--max-pages` still wins in the collector. Delays and
    concurrency are NOT configurable downwards here: pacing is per host (>= MIN_LIVE_DELAY_S, Crawl-delay honoured),
    one request at a time."""
    raw = os.getenv(f"WATCHTOWER_BUDGET_{source_key.upper()}")
    try:
        n = int(raw) if raw is not None else None
    except ValueError:
        n = None
    return min(n, MAX_PAGE_BUDGET) if n and n > 0 else default


MIN_LIVE_DELAY_S = 2.0  # floor for REAL merchants, whatever WATCHTOWER_MIN_DELAY says (crawl-delay can only raise it)

_PLACEHOLDER_HOSTS = {"example.com", "example.org", "example.net", "localhost", "invalid", "test", "domain.com", "yourdomain.com", "email.com"}


def contact_ok(contact: str | None = None) -> bool:
    """A real, reachable-looking contact (email or http(s) URL) that is not a documentation placeholder."""
    c = (CONTACT if contact is None else contact).strip()
    if not c or c.startswith("set-"):
        return False
    m = re.fullmatch(r"[^@\s]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", c) or re.fullmatch(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(/\S*)?", c)
    if not m:
        return False
    host = m.group(1).lower()
    return not any(host == h or host.endswith("." + h) for h in _PLACEHOLDER_HOSTS)
