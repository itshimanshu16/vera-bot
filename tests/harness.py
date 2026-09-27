"""Local end-to-end harness that mimics magicpin's evaluation (phases 1-4) against a running bot.

  python tests/harness.py [BOT_URL] [path/to/expanded] [--quiet]

Checks: warmup counts, idempotency (409), 12 ticks over all 100 triggers, adaptive injection,
reply personas (engaged / auto-reply / question / objection / off-topic / hostile / later / customer slot),
response schemas, latency, anti-repetition, determinism.
"""
import json
import sys
import time
from pathlib import Path
from urllib import request as rq, error as er

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
BOT = ARGS[0] if ARGS else "http://localhost:8080"
EXP = Path(ARGS[1]) if len(ARGS) > 1 else next((d for d in [Path(__file__).resolve().parents[2] / n / "expanded" for n in ("magicpin-ai-challenge", "challenge")] if d.exists()), Path("expanded"))
QUIET = "--quiet" in sys.argv
problems: list[str] = []
lat: dict[str, list[float]] = {}


def call(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = rq.Request(BOT + path, data=data, method=method, headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with rq.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode()), r.status
    except er.HTTPError as e:
        out = json.loads(e.read().decode() or "{}"), e.code
    lat.setdefault(path, []).append(time.time() - t)
    return out


def load(sub, key):
    return {json.loads(p.read_text(encoding="utf-8"))[key]: json.loads(p.read_text(encoding="utf-8")) for p in sorted((EXP / sub).glob("*.json"))}


cats = {json.loads(p.read_text(encoding="utf-8"))["slug"]: json.loads(p.read_text(encoding="utf-8")) for p in (EXP / "categories").glob("*.json")}
merchants, customers, triggers = load("merchants", "merchant_id"), load("customers", "customer_id"), load("triggers", "id")

ACTION_KEYS = {"conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name",
               "template_params", "body", "cta", "suppression_key", "rationale"}

# ---------------------------------------------------------------- phase 1: warmup
call("POST", "/v1/teardown")
h, _ = call("GET", "/v1/healthz")
assert h["status"] == "ok"
for slug, c in cats.items():
    call("POST", "/v1/context", {"scope": "category", "context_id": slug, "version": 1, "payload": c, "delivered_at": "2026-04-26T09:45:00Z"})
for mid, m in merchants.items():
    call("POST", "/v1/context", {"scope": "merchant", "context_id": mid, "version": 1, "payload": m, "delivered_at": "2026-04-26T09:45:00Z"})
for cid, c in customers.items():
    call("POST", "/v1/context", {"scope": "customer", "context_id": cid, "version": 1, "payload": c, "delivered_at": "2026-04-26T09:45:00Z"})
h, _ = call("GET", "/v1/healthz")
if h["contexts_loaded"] != {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}:
    problems.append(f"warmup counts wrong: {h['contexts_loaded']}")
r, code = call("POST", "/v1/context", {"scope": "merchant", "context_id": "m_001_drmeera_dentist_delhi", "version": 1,
                                        "payload": merchants["m_001_drmeera_dentist_delhi"], "delivered_at": "x"})
if code != 409 or r.get("reason") != "stale_version":
    problems.append(f"idempotency: expected 409 stale_version, got {code} {r}")
r, code = call("POST", "/v1/context", {"scope": "bogus", "context_id": "x", "version": 1, "payload": {}})
if code != 400:
    problems.append(f"invalid scope should be 400, got {code}")

# ---------------------------------------------------------------- phase 2+3: ticks with injections
tids = sorted(triggers)
per_tick = [tids[i::12] for i in range(12)]
sent_actions = []
all_bodies = {}
for i in range(12):
    now = f"2026-04-26T{10 + (i * 5) // 60:02d}:{(i * 5) % 60:02d}:00Z"
    for tid in per_tick[i]:
        call("POST", "/v1/context", {"scope": "trigger", "context_id": tid, "version": 1, "payload": triggers[tid], "delivered_at": now})
    if i == 4:  # adaptive injection: new digest item + a trigger that references it
        d2 = json.loads(json.dumps(cats["dentists"]))
        d2["digest"].append({"id": "d_NEW_hiv_pep", "kind": "compliance", "title": "IDA advisory: sharps-injury PEP protocol updated",
                             "source": "IDA circular 2026-04-20", "summary": "Post-exposure prophylaxis must start within 2 hours; clinics must display the protocol chart.",
                             "actionable": "Print and display the updated PEP chart in every operatory"})
        call("POST", "/v1/context", {"scope": "category", "context_id": "dentists", "version": 2, "payload": d2, "delivered_at": now})
        call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_NEW_pep", "version": 1, "delivered_at": now, "payload": {
            "id": "trg_NEW_pep", "scope": "merchant", "kind": "regulation_change", "source": "external",
            "merchant_id": "m_012_dr_karthik_dentist_mumbai", "customer_id": None,
            "payload": {"top_item_id": "d_NEW_hiv_pep"}, "urgency": 4, "suppression_key": "compliance:pep:2026", "expires_at": "2026-06-01T00:00:00Z"}})
        per_tick[i].append("trg_NEW_pep")
        m2 = json.loads(json.dumps(merchants["m_007_powerhouse_gym_bangalore"]))
        m2["performance"]["views"] = 999
        m2["performance"]["delta_7d"] = {"views_pct": -0.41, "calls_pct": -0.12}
        call("POST", "/v1/context", {"scope": "merchant", "context_id": "m_007_powerhouse_gym_bangalore", "version": 2, "payload": m2, "delivered_at": now})
        call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_NEW_dip_pw", "version": 1, "delivered_at": now, "payload": {
            "id": "trg_NEW_dip_pw", "scope": "merchant", "kind": "perf_dip", "source": "internal", "merchant_id": "m_007_powerhouse_gym_bangalore",
            "customer_id": None, "payload": {"metric": "views", "delta_pct": -0.41, "window": "7d"}, "urgency": 3,
            "suppression_key": "dip:m_007:new", "expires_at": "2026-06-01T00:00:00Z"}})
        per_tick[i].append("trg_NEW_dip_pw")
    if i == 6:  # surprise customer + recall 2 min later
        newc = {"customer_id": "c_NEW_meher", "merchant_id": "m_003_studio11_salon_hyderabad",
                "identity": {"name": "Meher", "language_pref": "hi-en mix"},
                "relationship": {"last_visit": "2026-02-10", "visits_total": 6, "services_received": ["hair_spa", "haircut"]},
                "state": "lapsed_soft", "preferences": {"preferred_slots": "weekday_evening", "channel": "whatsapp", "reminder_opt_in": True},
                "consent": {"opted_in_at": "2025-10-01", "scope": ["appointment_reminders", "promotional_offers"]}}
        call("POST", "/v1/context", {"scope": "customer", "context_id": "c_NEW_meher", "version": 1, "payload": newc, "delivered_at": now})
        call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_NEW_recall_meher", "version": 1, "delivered_at": now, "payload": {
            "id": "trg_NEW_recall_meher", "scope": "customer", "kind": "recall_due", "source": "internal",
            "merchant_id": "m_003_studio11_salon_hyderabad", "customer_id": "c_NEW_meher",
            "payload": {"service_due": "hair_spa_touchup", "last_service_date": "2026-02-10",
                        "available_slots": [{"iso": "2026-04-28T18:00:00+05:30", "label": "Tue 28 Apr, 6pm"},
                                            {"iso": "2026-04-29T19:00:00+05:30", "label": "Wed 29 Apr, 7pm"}]},
            "urgency": 3, "suppression_key": "recall:c_NEW_meher", "expires_at": "2026-05-30T00:00:00Z"}})
        per_tick[i].append("trg_NEW_recall_meher")
    active = sorted({t for j in range(i + 1) for t in per_tick[j]})  # active = everything pushed so far
    res, code = call("POST", "/v1/tick", {"now": now, "available_triggers": active})
    acts = res.get("actions", [])
    if len(acts) > 20:
        problems.append(f"tick {i}: {len(acts)} actions > 20")
    for a in acts:
        miss = ACTION_KEYS - set(a)
        if miss:
            problems.append(f"action missing keys {miss}")
        if not a.get("body"):
            problems.append(f"empty body {a.get('trigger_id')}")
        if "http" in a.get("body", ""):
            problems.append(f"URL in body {a.get('trigger_id')}")
        if a["body"] in all_bodies.values():
            problems.append(f"duplicate body across sends: {a['trigger_id']}")
        all_bodies[a["trigger_id"]] = a["body"]
        sent_actions.append(a)
    if not QUIET:
        print(f"tick {i:2} now={now} active={len(active):3} -> {len(acts)} actions")

sent_ids = {a["trigger_id"] for a in sent_actions}
print(f"\nSent {len(sent_actions)} actions for {len(sent_ids)} triggers of {len(triggers) + 3}")
unsent = sorted(set(triggers) - sent_ids)
print("Not sent:", unsent)
for t in ("trg_NEW_pep", "trg_NEW_dip_pw", "trg_NEW_recall_meher"):
    a = next((x for x in sent_actions if x["trigger_id"] == t), None)
    print(f"\n[injection] {t}:\n  {a['body'] if a else 'NOT SENT'}")
    if not a:
        problems.append(f"injection {t} not sent")
a = next((x for x in sent_actions if x["trigger_id"] == "trg_NEW_dip_pw"), None)
if a and "41%" not in a["body"]:
    problems.append("updated merchant perf (-41%) not used")

# ---------------------------------------------------------------- replies (phase 2 conversations + phase 4 replays)
by_trig = {a["trigger_id"]: a for a in sent_actions}


def convo(tid, script, label):
    a = by_trig.get(tid)
    if not a:
        print(f"\n[{label}] trigger {tid} not sent; skipping")
        return
    print(f"\n--- [{label}] {tid} ---\nBOT: {a['body'][:220]}{'...' if len(a['body']) > 220 else ''}")
    role = "customer" if a.get("customer_id") else "merchant"
    seen = {a["body"]}
    for k, msg in enumerate(script):
        r, code = call("POST", "/v1/reply", {"conversation_id": a["conversation_id"], "merchant_id": a["merchant_id"],
                                              "customer_id": a.get("customer_id"), "from_role": role, "message": msg,
                                              "received_at": "2026-04-26T11:00:00Z", "turn_number": k + 2})
        print(f"{role.upper()}: {msg}\n  -> {r.get('action')}" + (f" ({r.get('wait_seconds')}s)" if r.get("action") == "wait" else "")
              + (f": {r.get('body')}" if r.get("body") else "") + f"\n     [{r.get('rationale', '')[:110]}]")
        if r.get("action") not in ("send", "wait", "end"):
            problems.append(f"{label}: bad action {r}")
        if r.get("action") == "send":
            if not r.get("body"):
                problems.append(f"{label}: empty send body")
            if r.get("body") in seen:
                problems.append(f"{label}: repeated body")
            seen.add(r.get("body"))
        if r.get("action") == "end":
            break


convo("trg_001_research_digest_dentists", ["Yes please send the abstract", "Confirm", "Thanks"], "engaged dentist")
convo("trg_013_corporate_thali_planning", ["Looks good. How much will this cost me?", "ok let's do it", "confirm"], "question->commit")
convo("trg_004_perf_dip_bharat", ["Thank you for contacting Bharat Dental Care! Our team will respond shortly."] * 4, "auto-reply hell")
convo("trg_010_ipl_match_delhi", ["Why are you bothering me. This is useless.", "Btw can you help me file my GST this month?"], "hostile then off-topic")
convo("trg_020_summer_demand_shift", ["too expensive yaar, margins are low", "haan theek hai, kar do"], "objection hinglish")
convo("trg_008_curious_ask_studio11", ["Keratin treatment mostly", "yes"], "curious-ask answer")
convo("trg_009_winback_glamour", ["busy right now, talk later"], "later")
convo("trg_003_recall_due_priya", ["2"], "customer slot pick")
convo("trg_019_chronic_refill_grandfather", ["haan bhej do"], "customer refill yes")
convo("trg_NEW_recall_meher", ["can we do saturday instead?"], "customer reschedule")
convo("trg_022_cde_webinar_dentists", ["Not interested. Stop messaging me."], "opt-out")

# unknown conversation id (replay may start fresh)
r, _ = call("POST", "/v1/reply", {"conversation_id": "conv_replay_x", "merchant_id": "m_006_southindiancafe_restaurant_bangalore",
                                  "from_role": "merchant", "message": "Ok, let's do it. What's next?", "received_at": "2026-04-26T11:00:00Z", "turn_number": 3})
print(f"\n--- [intent on unknown conv] ---\n  -> {r}")
low = (r.get("body") or "").lower()
if any(q in low for q in ["would you", "do you", "can you tell", "what if", "how about"]):
    problems.append("intent transition still qualifying")

# ---------------------------------------------------------------- determinism: new session, same inputs -> same bodies
call("POST", "/v1/teardown")
for slug, c in cats.items():
    call("POST", "/v1/context", {"scope": "category", "context_id": slug, "version": 1, "payload": c, "delivered_at": "x"})
for mid, m in merchants.items():
    call("POST", "/v1/context", {"scope": "merchant", "context_id": mid, "version": 1, "payload": m, "delivered_at": "x"})
for cid, c in customers.items():
    call("POST", "/v1/context", {"scope": "customer", "context_id": cid, "version": 1, "payload": c, "delivered_at": "x"})
same = diff = 0
for tid in tids[:40]:
    call("POST", "/v1/context", {"scope": "trigger", "context_id": tid, "version": 1, "payload": triggers[tid], "delivered_at": "x"})
res, _ = call("POST", "/v1/tick", {"now": "2026-04-26T10:00:00Z", "available_triggers": tids[:40]})
for a in res["actions"]:
    if a["trigger_id"] in all_bodies and a["trigger_id"] not in ("trg_NEW_dip_pw",):
        if all_bodies[a["trigger_id"]] == a["body"]:
            same += 1
        else:
            diff += 1
print(f"\nDeterminism: {same} identical, {diff} different (merchant-state changes can legitimately differ)")

# ---------------------------------------------------------------- latency
print("\nLatency (max seconds):", {k: round(max(v), 3) for k, v in lat.items()})
print("\nPROBLEMS:" if problems else "\nNo problems found.")
for p in problems:
    print(" -", p)
