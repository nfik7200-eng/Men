# Performance (measured, synthetic scale test)

`python scripts/perf_bench.py 5000` builds a SYNTHETIC temporary database (5,000 listings, 15,000 observations, 10,000 crawl rows,
4.6 MB) and times the user-facing queries. Synthetic numbers measure speed only, never data quality. Dev container: 4 vCPU, 16 GB RAM.

| Query | Before | After | Peak memory |
|---|---|---|---|
| search "board" (page 1) | 41 ms | 30 ms | 0.4 MB |
| top_deals(20) | 1.4 s | 1.4 s | 49 MB |
| assistant "cheapest synthetic board" | 5.2 s | 2.1 s cold / 0.9 s warm | 51 MB |
| assistant "which products have insufficient price history" | 11.1 s | 1.7 s | 54 MB |
| coverage report (with next-in-line) | 1.3 s | 1.1 s (public page cached 30 s) | 28 MB |
| integrity report (read-only SQL) | 0.5 s | 0.4 s | n/a |

Cause fixed: the assistant ran one history query per candidate listing (≈2 queries/listing); it now loads every candidate's
observations in one query (`tests/test_assistant_units.py::test_assistant_query_count_does_not_grow_with_the_number_of_listings`).
Real data today is 282 live listings (assistant answers 20-130 ms). Remaining cost is linear ORM loading; if the catalogue
grows far beyond ~10,000 listings the next step is SQL-side candidate pre-filtering (not needed yet).
Resource sizing for the VPS: the web process peaked at tens of MB per request in these tests; the response-size cap is 16 MB per page and 512 KiB for robots.txt.
