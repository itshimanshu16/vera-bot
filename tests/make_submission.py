"""Write submission.jsonl — one composed message per canonical test pair (brief §7.2).

Usage: python tests/make_submission.py [path/to/expanded] [out=submission.jsonl]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vera.composer import compose  # noqa: E402

exp = Path(sys.argv[1]) if len(sys.argv) > 1 else next((d for d in [Path(__file__).resolve().parents[2] / n / "expanded" for n in ("magicpin-ai-challenge", "challenge")] if d.exists()), Path("expanded"))
out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "submission.jsonl"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


pairs = load(exp / "test_pairs.json")["pairs"]
lines = []
for p in pairs:
    trg = load(exp / "triggers" / f"{p['trigger_id']}.json")
    m = load(exp / "merchants" / f"{p['merchant_id']}.json")
    cat = load(exp / "categories" / f"{m['category_slug']}.json")
    cust = load(exp / "customers" / f"{p['customer_id']}.json") if p.get("customer_id") else None
    out = compose(cat, m, trg, cust, "2026-04-26T10:30:00Z")
    if out.get("skip"):
        row = {"test_id": p["test_id"], "body": "", "cta": "none", "send_as": "merchant_on_behalf",
               "suppression_key": out["suppression_key"], "rationale": f"Not sent: {out['skip_reason']}"}
    else:
        row = {"test_id": p["test_id"], **{k: out[k] for k in ("body", "cta", "send_as", "suppression_key", "rationale")}}
    lines.append(json.dumps(row, ensure_ascii=False))
out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {len(lines)} lines to {out_path}")
