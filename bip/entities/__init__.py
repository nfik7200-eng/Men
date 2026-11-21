"""Entities and the evidence-backed relationships between them.

Relationships are *observed* from stored canonical transactions (or marked *hypothesis* when they
are an inference). They never assert that two addresses share an owner: a funder may be an
exchange, a shared counterparty may be a protocol, and a deployer only sent the creation
transaction. Verified attribution is not produced here.
"""
