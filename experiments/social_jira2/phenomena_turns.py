#!/usr/bin/env python3
"""Per-turn evidence reader for social_jira2 judge results (inverse of phenomena_view.py).

Thin adapter over ``experiments.social_common.phenomena_turns``: reuses this experiment's
``ADAPTER`` (axes / taxonomy / roots) from ``phenomena_view`` and launches the Flask evidence
server. Filter by any axis, pick phenomena as columns, click a turn's "show more" to read the
whole rollout beside it.

Usage:
    python -m experiments.social_jira2.phenomena_turns [<root> ...] [--port 5003]
    # then open http://127.0.0.1:5003
"""
from experiments.social_common.phenomena_turns import run_server
from experiments.social_jira2.phenomena_view import ADAPTER

if __name__ == "__main__":
    run_server(ADAPTER, default_port=5003)
