"""Advisory ML / data-science harness for Watchtower.

Hard guarantees (enforced by tests/test_ml_*.py):
  * Nothing here writes to the database, merges products, or influences ranking. Scores are ADVISORY only.
  * The app and the core test-suite never import this package; scikit-learn is optional and imported lazily.
  * Only listings of Source.kind == 'live' are used as evidence. Demo/sandbox rows are excluded everywhere.
  * Models refuse to train / emit verdicts when the data is insufficient (see `sufficiency.sufficiency_report`).
  * `watchtower` never imports `shopping`; so neither does this package.
"""
ML_SCHEMA_VERSION = "1"
ADVISORY_ONLY = True  # models must never be used to auto-merge products (CLAUDE.md: uncertain -> review queue)
