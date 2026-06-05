"""Crypto broker adapters — Deribit connectivity, discovery, and market-data collection.

Broker-specific edge layer that feeds the broker-agnostic ``algotrading.infra`` analytics
pipeline. Nothing in this package implements analytics; it only translates Deribit's wire
format into the canonical types (``BrokerTick``, ``OptionContract``) that the rest of the
stack consumes.
"""
