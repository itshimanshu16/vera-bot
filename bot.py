"""Brief §7.1 interface: compose() and respond() as plain functions.

The HTTP server (app.py) uses the same code; this module just exposes it in the
shape the challenge brief describes.
"""
from typing import Optional

from vera.composer import compose as _compose


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
    """Returns {body, cta, send_as, suppression_key, rationale}. Deterministic, no network calls."""
    out = _compose(category, merchant, trigger, customer)
    if out.get("skip"):
        return {"body": "", "cta": "none", "send_as": "merchant_on_behalf" if customer else "vera",
                "suppression_key": out["suppression_key"], "rationale": f"Not sent: {out['skip_reason']}"}
    return {k: out[k] for k in ("body", "cta", "send_as", "suppression_key", "rationale")}
