"""Loyalty assistant orchestration: routing, engines, tools, LLM and safety layers.

This package has no web framework dependency; the FastAPI backend is its only
caller and supplies the trusted customer_id for every turn.
"""
