"""Minimal RFC 9309 robots.txt parser, independent of the Python version.

Why not urllib.robotparser: older CPython versions treat a blank line as the end of a group (Adafruit's file has one
right after `User-agent: *`, so every rule is ignored) and ignore `*` / `$` wildcards (SparkFun's file uses them), which
would silently disable our compliance checks. Rules: groups are chosen by the most specific matching user-agent token
(else `*`); within a group the LONGEST matching pattern wins and Allow wins a tie; `*` matches any run of characters and
a trailing `$` anchors the end; an empty Disallow allows everything; no robots file => allow."""
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay: float | None = None


MAX_ROBOTS_BYTES = 512 * 1024   # RFC 9309: parsers may ignore content beyond 500 KiB
MAX_RULES_PER_GROUP = 5000
MAX_PATTERN_LEN = 512
MAX_PATH_LEN = 4096


def _matches(pattern: str, path: str) -> bool:
    """Robots path matching WITHOUT regular expressions: `*` = any run of characters, a trailing `$` anchors the end, otherwise
    the pattern is a prefix. Greedy left-to-right search is correct for prefix semantics and runs in O(parts x len(path)), so a
    hostile pattern like `/*a*a*a*a*b` cannot trigger the polynomial blow-up the former regex translation had."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = body.split("*")
    if len(parts) == 1:
        return path == body if anchored else path.startswith(body)
    if not path.startswith(parts[0]):
        return False
    pos = len(parts[0])
    for part in parts[1:-1]:
        i = path.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    last = parts[-1]
    if anchored:
        return len(path) - pos >= len(last) and path.endswith(last)
    return path.find(last, pos) >= 0


class Robots:
    def __init__(self, text: str | None):
        self.groups: list[Group] = []
        self.sitemaps: list[str] = []
        self.present = text is not None
        if text:
            self._parse(text[:MAX_ROBOTS_BYTES])

    def _parse(self, text: str) -> None:
        cur: Group | None = None
        last_was_agent = False
        for raw in text.replace("\r", "\n").split("\n"):
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue  # blank lines do NOT end a group (RFC 9309)
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if cur is None or not last_was_agent:
                    cur = Group()
                    self.groups.append(cur)
                cur.agents.append(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if key == "sitemap":
                self.sitemaps.append(value)
            elif cur is None:
                continue
            elif key in ("allow", "disallow"):
                if value == "" and key == "disallow":
                    continue  # empty Disallow = allow all
                if len(cur.rules) < MAX_RULES_PER_GROUP:
                    cur.rules.append((key == "allow", value[:MAX_PATTERN_LEN]))
            elif key == "crawl-delay":
                try:
                    cur.crawl_delay = float(value)
                except ValueError:
                    pass

    def _group_for(self, user_agent: str) -> Group | None:
        token = re.split(r"[/\s(]", user_agent.strip().lower(), maxsplit=1)[0]
        best, best_len = None, -1
        for g in self.groups:
            for a in g.agents:
                if a == "*":
                    if best is None:
                        best, best_len = g, 0
                elif a and a == token:  # RFC 9309: the group's product token must equal ours (case-insensitive), not merely overlap it
                    if len(a) > best_len:
                        best, best_len = g, len(a)
        return best

    def can_fetch(self, user_agent: str, url: str) -> bool:
        if not self.present:
            return True
        g = self._group_for(user_agent)
        if g is None:
            return True
        p = urlparse(url)
        path = (unquote(p.path or "/") + (f"?{p.query}" if p.query else ""))[:MAX_PATH_LEN]
        best_len, allowed = -1, True
        for allow, pattern in g.rules:
            if _matches(unquote(pattern), path):
                length = len(pattern)
                if length > best_len or (length == best_len and allow):
                    best_len, allowed = length, allow
        return allowed

    def crawl_delay(self, user_agent: str) -> float | None:
        g = self._group_for(user_agent)
        return g.crawl_delay if g else None
