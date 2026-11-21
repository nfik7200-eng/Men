"""Autonomous discovery: detectors over stored canonical data, scored, deduplicated signals.

Pipeline: stored evidence -> detector -> candidate -> explainable score -> deduplication and
cooldown -> signal (discovery feed) -> alert, watchlist or case. Nothing here contacts a provider:
detectors read only what ingestion already stored, never orphaned rows, and every signal declares
what it covers and what it cannot see.
"""

DISCOVERY_VERSION = "1"
