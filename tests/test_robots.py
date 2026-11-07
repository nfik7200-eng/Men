"""Own RFC 9309 parser, checked against the REAL robots.txt files of the approved/rejected candidates."""
import pytest

from conftest import REAL
from watchtower.robots import Robots

UA = "WatchtowerBot/0.1 (price-research; contact: ops@watchtower-research.net)"


def _r(name):
    return Robots((REAL / f"{name}.robots.txt").read_text())


@pytest.mark.parametrize("url,ok", [("https://www.adafruit.com/product/5812", True), ("https://www.adafruit.com/category/105", True),
                                    ("https://www.adafruit.com/api/x", False), ("https://www.adafruit.com/ajax/x", False),
                                    ("https://www.adafruit.com/search", False), ("https://www.adafruit.com/search?q=pi", False),
                                    ("https://www.adafruit.com/includes/javascript/a.js", True), ("https://www.adafruit.com/includes/secret.php", False)])
def test_adafruit_real_robots_blank_line_after_user_agent_does_not_drop_the_group(url, ok):
    assert _r("www.adafruit.com").can_fetch(UA, url) is ok  # stdlib parsers of some Python versions ignore this whole group


@pytest.mark.parametrize("url,ok", [("https://www.sparkfun.com/raspberry-pi-zero-2-w.html", True), ("https://www.sparkfun.com/media/sitemap.xml", True),
                                    ("https://www.sparkfun.com/checkout", False), ("https://www.sparkfun.com/search?q=pi", False),
                                    ("https://www.sparkfun.com/searchfoo", False),                        # Disallow: /search* (wildcard)
                                    ("https://www.sparkfun.com/raspberry-pi-4-model-b-2gb.html?itemsPerPage=50", False),  # /*?itemsPerPage=
                                    ("https://www.sparkfun.com/catalog/category/view/id/5", False),
                                    ("https://www.sparkfun.com/catalog/product/view/id/5", True),          # longer Allow beats Disallow: /catalog/product/view/
                                    ("https://www.sparkfun.com/api/x", False)])
def test_sparkfun_real_robots_wildcards_and_longest_match(url, ok):
    assert _r("www.sparkfun.com").can_fetch(UA, url) is ok


def test_sparkfun_crawl_delay_and_bot_specific_groups():
    r = _r("www.sparkfun.com")
    assert r.crawl_delay(UA) == 10.0
    assert r.can_fetch("YandexBot/3.0", "https://www.sparkfun.com/anything.html") is False  # a named group applies only to that bot
    assert r.can_fetch("Googlebot", "https://www.sparkfun.com/search") is True


def test_pihut_robots_allows_crawl_even_though_its_terms_forbid_it():
    r = _r("thepihut.com")
    assert r.can_fetch(UA, "https://thepihut.com/products/raspberry-pi-5") is True and r.can_fetch(UA, "https://thepihut.com/cart/") is False


def test_edge_cases():
    assert Robots(None).can_fetch(UA, "https://x/anything") is True                                    # no robots.txt => allowed
    assert Robots("User-agent: *\nDisallow:\n").can_fetch(UA, "https://x/a") is True                    # empty Disallow
    assert Robots("User-agent: *\nDisallow: /\n").can_fetch(UA, "https://x/a") is False
    r = Robots("User-agent: a\nUser-agent: *\nDisallow: /p/\nAllow: /p/ok$\n")                          # stacked agents share rules
    assert r.can_fetch(UA, "https://x/p/no") is False and r.can_fetch(UA, "https://x/p/ok") is True and r.can_fetch(UA, "https://x/p/ok2") is False
    assert Robots("User-agent: *\r\nDisallow: /x\r\n").can_fetch(UA, "https://x/x") is False           # CRLF
    assert Robots("# c\nuser-agent: *   # trailing\n\nDISALLOW: /z\n").can_fetch(UA, "https://x/z") is False  # case/comments/blank line
    r2 = Robots("User-agent: watchtowerbot\nDisallow: /\nUser-agent: *\nDisallow:\n")
    assert r2.can_fetch(UA, "https://x/a") is False                                                     # most specific group wins over *


# --- security review A1: wildcard patterns must match in linear time ------------------------------------------------
import time  # noqa: E402


def test_pathological_wildcard_patterns_cannot_freeze_the_process():
    r = Robots("User-agent: *\nDisallow: /*a*a*a*a*a*a*b\n")
    t = time.perf_counter()
    assert r.can_fetch(UA, "https://x.test/" + "a" * 3000) is True  # no 'b': the rule does not match
    assert time.perf_counter() - t < 0.5  # was 22 s at 300 characters with the regex implementation


@pytest.mark.parametrize("rule,path,blocked", [
    ("/private", "/private/x", True), ("/private", "/privat", False), ("/*.php$", "/a/b.php", True), ("/*.php$", "/a/b.php?x=1", False),
    ("/a*b*c", "/aXXbYYc/z", True), ("/a*b*c", "/aXXcYYb", False), ("/*?itemsPerPage=", "/p.html?itemsPerPage=50", True),
    ("/x$", "/x", True), ("/x$", "/xy", False), ("/*", "/anything", True), ("/a**b", "/ab", True),
])
def test_wildcard_and_anchor_semantics_are_unchanged(rule, path, blocked):
    assert Robots(f"User-agent: *\nDisallow: {rule}\n").can_fetch(UA, "https://x.test" + path) is (not blocked)


def test_robots_text_and_rule_counts_are_capped():
    huge = "User-agent: *\n" + "".join(f"Disallow: /p{i}\n" for i in range(50000)) + "Disallow: /late-rule-after-the-cap\n" * 5
    t = time.perf_counter()
    r = Robots(huge)
    r.can_fetch(UA, "https://x.test/zzz")
    assert time.perf_counter() - t < 2.0 and sum(len(g.rules) for g in r.groups) <= 5000
    assert Robots("User-agent: *\n" + "#" * (2 * 1024 * 1024) + "\nDisallow: /secret\n").can_fetch(UA, "https://x.test/secret") is True  # beyond 512 KiB: ignored (RFC 9309)
