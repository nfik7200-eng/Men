"""Content-addressed, manifest-committed file lake (roadmap Stage 5, ADR-055).

The manifest line is the commit point: a data file without a valid manifest entry is
uncommitted and never read. Nothing in this package deletes or overwrites a data file.
"""
