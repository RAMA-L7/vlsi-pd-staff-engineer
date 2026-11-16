"""Deterministic, evidence-bound supervision for the VLSI PD assistant.

This package intentionally contains NO LLM calls: report parsing, evidence
bookkeeping and gate decisions are deterministic.  Summaries produced by an
LLM (or a human) are stored as *advisory* text only and can never authorize a
gate transition.
"""

__version__ = "0.1.0"
