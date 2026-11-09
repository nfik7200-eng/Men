"""Test-only builders. EVERYTHING built here is a SYNTHETIC TEST FIXTURE, never evidence about the real data."""
from datetime import datetime, timedelta

from watchtower.models import Listing, Observation, Product, Source


def mk_source(s, key, kind="live"):
    src = Source(key=key, name=key, base_url=f"https://{key}.example", country="US", currency="USD", kind=kind)
    s.add(src)
    s.flush()
    return src


def mk_listing(s, src, ext, title, brand="Acme", product=None, status="self", price=1000, **kw):
    l = Listing(source_id=src.id, external_id=ext, url=f"https://{src.key}.example/{ext}", title=title, brand=brand,
                category="maker-electronics", currency="USD", price_minor=price, match_status=status,
                product_id=product.id if product else None, **kw)
    s.add(l)
    s.flush()
    return l


def mk_obs(s, listing, at, price):
    s.add(Observation(listing_id=listing.id, observed_at=at, price_minor=price, currency="USD"))


def mk_product(s, title="P"):
    p = Product(title=title)
    s.add(p)
    s.flush()
    return p


def small_catalogue(s):
    """Two live merchants, three cross-merchant products (auto-linked) plus one demo listing. Synthetic."""
    a, b, demo = mk_source(s, "shopa"), mk_source(s, "shopb"), mk_source(s, "demoshop", "demo")
    out = []
    for i, (ta, tb) in enumerate([("Acme Widget 2GB", "Acme Widget - 2 GB"), ("Acme Widget 4GB", "Acme Widget - 4 GB"),
                                  ("Acme Gadget 8GB", "Acme Gadget (8 GB)")]):
        p = mk_product(s, ta)
        out.append((mk_listing(s, a, f"a{i}", ta, product=p, status="auto"), mk_listing(s, b, f"b{i}", tb, product=p)))
    mk_listing(s, demo, "d0", "Acme Widget 2GB")
    for i in range(3):  # unrelated singletons so negatives exist
        mk_listing(s, a, f"x{i}", f"Acme Other{i} Thing {i}")
    return a, b, demo, out
