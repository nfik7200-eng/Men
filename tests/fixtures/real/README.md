Real captures (2026-11-06), fetched with User-Agent `WatchtowerBot/0.1 (price-research; contact: ...)`, honouring robots.txt and crawl-delay.
- `ada_*.html`, `spark_*.html` are REDUCED captures of real product pages: only the elements our parsers read are kept (title, price meta, JSON-LD
  with description/image fields blanked, breadcrumbs, stock badge, product id). Product descriptions and images were removed on purpose: we do not
  store them (copyright), so the repository must not either. Structure and values of everything kept are unchanged.
- `*_sitemap_excerpt.xml`: selected `<url>` entries of the real sitemaps. `*.robots.txt`: complete robots.txt files.
Prices in these files are historical observations, not current.
