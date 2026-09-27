"""Compose a message for every trigger in the expanded dataset and print it.

Usage: python tests/dump_all.py [path/to/expanded] [--only T01,T02] [--now 2026-04-26T10:00:00Z]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from vera.composer import compose  # noqa: E402


def load(exp: Path):
    cats = {json.loads(p.read_text(encoding="utf-8"))["slug"]: json.loads(p.read_text(encoding="utf-8")) for p in (exp / "categories").glob("*.json")}
    ms = {json.loads(p.read_text(encoding="utf-8"))["merchant_id"]: json.loads(p.read_text(encoding="utf-8")) for p in (exp / "merchants").glob("*.json")}
    cs = {json.loads(p.read_text(encoding="utf-8"))["customer_id"]: json.loads(p.read_text(encoding="utf-8")) for p in (exp / "customers").glob("*.json")}
    ts = {json.loads(p.read_text(encoding="utf-8"))["id"]: json.loads(p.read_text(encoding="utf-8")) for p in (exp / "triggers").glob("*.json")}
    return cats, ms, cs, ts


def main():
    skip_next = {i + 1 for i, a in enumerate(sys.argv) if a in ("--now", "--only")}
    args = [a for i, a in enumerate(sys.argv) if i > 0 and not a.startswith("--") and i not in skip_next]
    exp = Path(args[0]) if args else next((d for d in [Path(__file__).resolve().parents[2] / n / "expanded" for n in ("magicpin-ai-challenge", "challenge")] if d.exists()), Path("expanded"))
    now = "2026-04-26T10:30:00Z"
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--now":
            now = sys.argv[i + 1]
        if a == "--only":
            only = set(sys.argv[i + 1].split(","))
    cats, ms, cs, ts = load(exp)
    pairs = json.loads((exp / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]
    tid_to_test = {p["trigger_id"]: p["test_id"] for p in pairs}
    for tid in sorted(ts):
        t = ts[tid]
        tag = tid_to_test.get(tid, "")
        if only and tag not in only and tid not in only:
            continue
        m = ms.get(t["merchant_id"])
        c = cs.get(t.get("customer_id")) if t.get("customer_id") else None
        cat = cats.get(m["category_slug"]) if m else None
        out = compose(cat, m, t, c, now)
        print(f"=== {tag:3} {tid}  [{t['kind']}] {m['identity']['name']}")
        if out.get("skip"):
            print("   SKIP:", out.get("skip_reason"))
            continue
        print(out["body"])
        print(f"   cta={out['cta']} send_as={out['send_as']} len={len(out['body'])}")
        print(f"   why: {out['rationale']}")
        print()


if __name__ == "__main__":
    main()
