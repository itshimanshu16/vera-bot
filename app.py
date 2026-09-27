"""Vera bot — HTTP server for the magicpin AI Challenge.

Endpoints: GET /v1/healthz, GET /v1/metadata, POST /v1/context, POST /v1/tick,
           POST /v1/reply, POST /v1/teardown (optional wipe)

Run locally:  uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from vera.composer import compose
from vera.facts import Facts
from vera.llm import LLM
from vera.replies import respond
from vera.store import SCOPES, Store
from vera.util import parse_date, stable_hash

VERSION = "1.0.0"
START = time.time()
STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
TICK_BUDGET = float(os.getenv("TICK_BUDGET_SECONDS", "9"))
MAX_ACTIONS = 20

store = Store(os.getenv("STATE_FILE", "vera_state.json"))
llm = LLM()
pool = ThreadPoolExecutor(max_workers=int(os.getenv("LLM_WORKERS", "12")))

app = FastAPI(title="Vera bot", version=VERSION)

KIND_WEIGHT = {  # tie-breaker after urgency: compliance/safety first, curiosity last
    "supply_alert": 9, "regulation_change": 8, "compliance_alert": 8, "chronic_refill_due": 7, "recall_due": 7,
    "appointment_tomorrow": 7, "active_planning_intent": 7, "renewal_due": 6, "perf_dip": 6, "ipl_match_today": 6,
    "review_theme_emerged": 5, "competitor_opened": 5, "customer_lapsed_hard": 5, "customer_lapsed_soft": 4,
    "wedding_package_followup": 4, "trial_followup": 4, "winback_eligible": 4, "gbp_unverified": 4,
    "category_seasonal": 3, "seasonal_perf_dip": 3, "perf_spike": 3, "research_digest": 3, "cde_opportunity": 2,
    "milestone_reached": 2, "festival_upcoming": 2, "dormant_with_vera": 2, "curious_ask_due": 1,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def bad(reason: str, details: str = "", code: int = 400) -> JSONResponse:
    return JSONResponse({"accepted": False, "reason": reason, "details": details}, status_code=code)


# --------------------------------------------------------------------------- health / metadata

@app.get("/")
def root():
    return {"service": "vera-bot", "version": VERSION, "endpoints": ["/v1/healthz", "/v1/metadata", "/v1/context", "/v1/tick", "/v1/reply"]}


@app.get("/v1/healthz")
def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": store.counts()}


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": os.getenv("TEAM_NAME", "Shaurya Bhardwaj"),
        "team_members": [m.strip() for m in os.getenv("TEAM_MEMBERS", "Shaurya Bhardwaj").split(",")],
        "model": llm.describe(),
        "approach": ("Hybrid: deterministic rules engine picks the single strongest signal per trigger kind and composes from "
                     "verified context facts (no invented data, one CTA, category voice, language pref); optional LLM rewrite is "
                     "number-validated against the contexts with automatic fallback. Stateful reply engine: auto-reply ladder, "
                     "intent->action switch, opt-out/hostile exits, off-topic redirect."),
        "contact_email": os.getenv("CONTACT_EMAIL", "shauryaarunbhardwaj@gmail.com"),
        "version": VERSION,
        "submitted_at": os.getenv("SUBMITTED_AT", STARTED_AT),
    }


# --------------------------------------------------------------------------- context

@app.post("/v1/context")
async def push_context(request: Request):
    try:
        body = await request.json()
    except Exception:
        return bad("invalid_json", "body is not valid JSON")
    if not isinstance(body, dict):
        return bad("invalid_body", "expected a JSON object")
    scope = body.get("scope")
    cid = body.get("context_id")
    version = body.get("version")
    payload = body.get("payload")
    if scope not in SCOPES:
        return bad("invalid_scope", f"scope must be one of {list(SCOPES)}")
    if not isinstance(cid, str) or not cid.strip():
        return bad("invalid_context_id", "context_id must be a non-empty string")
    try:
        version = int(version)
    except (TypeError, ValueError):
        return bad("invalid_version", "version must be an integer")
    if not isinstance(payload, dict):
        return bad("invalid_payload", "payload must be an object")
    accepted, cur = store.put(scope, cid, version, payload)
    if not accepted:
        return JSONResponse({"accepted": False, "reason": "stale_version", "current_version": cur}, status_code=409)
    return {"accepted": True, "ack_id": f"ack_{cid}_v{version}", "stored_at": now_iso()}


# --------------------------------------------------------------------------- tick

def _expired(trg: dict, now: Optional[str]) -> bool:
    exp, n = parse_date(trg.get("expires_at")), parse_date(now)
    if not exp or not n:
        return False
    try:
        return exp < n
    except TypeError:
        return exp.replace(tzinfo=None) < n.replace(tzinfo=None)


def _conv_id(trg_id: str) -> str:
    base = "conv_" + re.sub(r"[^a-zA-Z0-9_]+", "_", trg_id)[:80]
    cid, i = base, 2
    while cid in store.conversations:
        cid, i = f"{base}_{i}", i + 1
    return cid


def _history_action(merchant: dict) -> Optional[str]:
    """The last thing Vera offered this merchant in their stored history, if any."""
    hist = merchant.get("conversation_history") or []
    for h in reversed(hist):
        if str(h.get("from")) == "vera":
            m = re.search(r"Want me to (.+?)\?", str(h.get("body", "")))
            if m:
                act = m.group(1).strip()
                last_m = next((x for x in reversed(hist) if str(x.get("from")) == "merchant"), None)
                if last_m:
                    fm = re.search(r"focus on (.+)$", str(last_m.get("body", "")), re.I)
                    if fm:
                        act += f" (focused on {fm.group(1).strip().rstrip('.')})"
                return act
    return None


SESSION_GAP = float(os.getenv("SESSION_GAP_SECONDS", "900"))


def _maybe_new_session(now: str):
    """A new judge run (simulated clock jumped backwards, or >15 min of real-time silence)
    must not inherit the previous run's dedup / opt-out state. Contexts are kept."""
    with store.lock:
        last_real = store.session.get("last_tick_real")
        last_now = parse_date(store.session.get("last_tick_now"))
        cur = parse_date(now)
        backwards = False
        if last_now and cur:
            try:
                backwards = cur < last_now
            except TypeError:
                backwards = cur.replace(tzinfo=None) < last_now.replace(tzinfo=None)
        if last_real and (backwards or time.time() - last_real > SESSION_GAP):
            store.new_session()
        store.session["last_tick_real"] = time.time()
        store.session["last_tick_now"] = now


@app.post("/v1/tick")
async def tick(request: Request):
    t0 = time.time()
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    now = body.get("now") or now_iso()
    available = [t for t in (body.get("available_triggers") or []) if isinstance(t, str)]
    _maybe_new_session(now)

    with store.lock:
        deferred = [t for t in store.deferred if t not in available]
    cand_ids = list(dict.fromkeys(available + deferred))
    if not cand_ids:
        # no hint from the judge: consider stored, unexpired, unsent triggers
        cand_ids = [tid for tid, trg in store.all("trigger").items() if not _expired(trg, now)]

    cands = []
    keep_deferred = []
    for tid in cand_ids:
        trg = store.get("trigger", tid)
        if not trg:
            continue
        sk = trg.get("suppression_key") or tid
        if tid in store.sent_triggers or sk in store.sent_keys:
            continue
        if tid in deferred and tid not in available and _expired(trg, now):
            continue
        mid = trg.get("merchant_id") or (trg.get("payload") or {}).get("merchant_id")
        merchant = store.get("merchant", mid)
        if not merchant:
            keep_deferred.append(tid)  # merchant context may arrive later
            continue
        category = store.get("category", merchant.get("category_slug")) or {}
        cust_id = trg.get("customer_id")
        customer = store.get("customer", cust_id) if cust_id else None
        if cust_id and not customer:
            keep_deferred.append(tid)  # never message a customer we have no consent record for
            continue
        if cust_id and store.cstate(cust_id).get("opted_out"):
            continue
        if not cust_id and store.mstate(mid).get("opted_out"):
            continue
        try:
            out = compose(category, merchant, trg, customer, now)
        except Exception as e:  # never let one bad context break the tick
            print(f"[tick] compose failed for {tid}: {e!r}")
            continue
        if out.get("skip"):
            store.sent_triggers.add(tid)  # consciously not sending; don't reconsider every tick
            continue
        urg = trg.get("urgency") or 1
        try:
            urg = int(urg)
        except (TypeError, ValueError):
            urg = 1
        prio = (-urg, -KIND_WEIGHT.get(str(trg.get("kind")), 3), 0 if tid in available else 1, tid)
        cands.append((prio, tid, trg, merchant, category, customer, out))

    cands.sort(key=lambda x: x[0])
    chosen, used_merchants, used_customers = [], set(), set()
    for c in cands:
        _, tid, trg, merchant, category, customer, out = c
        mid = merchant.get("merchant_id") or trg.get("merchant_id")
        recipient = ("c", customer.get("customer_id")) if customer else ("m", mid)
        if len(chosen) >= MAX_ACTIONS or (recipient[0] == "m" and mid in used_merchants) or \
                (recipient[0] == "c" and recipient[1] in used_customers):
            keep_deferred.append(tid)
            continue
        bkey = stable_hash([recipient, out["body"]])
        if bkey in store.sent_bodies:
            store.sent_triggers.add(tid)  # identical message already delivered to this recipient: skip, don't spam
            continue
        (used_customers if customer else used_merchants).add(recipient[1])
        store.sent_bodies.add(bkey)
        chosen.append(c)

    # optional LLM pass, bounded by the tick budget; fallback = deterministic draft
    if llm.enabled and chosen:
        futs = {}
        for c in chosen:
            _, tid, trg, merchant, category, customer, out = c
            f = Facts(category, merchant, trg, customer, now)
            futs[pool.submit(llm.compose, f, out["body"], out.get("known_kind", True), out["cta"])] = c
        remaining = max(0.5, TICK_BUDGET - (time.time() - t0))
        done, _ = wait(list(futs), timeout=remaining)
        for fu in done:
            try:
                res = fu.result()
            except Exception:
                res = None
            if res:
                futs[fu][6]["body"] = res
                futs[fu][6]["rationale"] += " (wording refined by LLM; facts validated)"

    actions = []
    with store.lock:
        for _, tid, trg, merchant, category, customer, out in chosen:
            mid = merchant.get("merchant_id") or trg.get("merchant_id")
            conv_id = _conv_id(tid)
            action = {
                "conversation_id": conv_id,
                "merchant_id": mid,
                "customer_id": customer.get("customer_id") if customer else None,
                "send_as": out["send_as"],
                "trigger_id": tid,
                "template_name": out["template_name"],
                "template_params": out["template_params"],
                "body": out["body"],
                "cta": out["cta"],
                "suppression_key": out["suppression_key"],
                "rationale": out["rationale"],
            }
            actions.append(action)
            store.sent_triggers.add(tid)
            store.sent_keys.add(out["suppression_key"])
            m = re.search(r"Want me to (.+?)\? Reply", out["body"])
            slots = [s.get("label") for s in (trg.get("payload") or {}).get("available_slots") or [] if isinstance(s, dict)]
            slots += [s.get("label") for s in (trg.get("payload") or {}).get("next_session_options") or [] if isinstance(s, dict)]
            store.conversations[conv_id] = {
                "merchant_id": mid, "customer_id": action["customer_id"], "trigger_id": tid,
                "kind": trg.get("kind"), "send_as": out["send_as"],
                "role": "customer" if customer else "merchant", "stage": 0,
                "offer_action": m.group(1) if m else None, "slots": [s for s in slots if s],
                "turns": [{"from": "bot", "msg": out["body"], "ts": now}], "created": now,
            }
        store.deferred = list(dict.fromkeys(keep_deferred))
        store.mark_dirty()
    store.save_soon()
    return {"actions": actions}


# --------------------------------------------------------------------------- reply

@app.post("/v1/reply")
async def reply(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"action": "wait", "wait_seconds": 600, "rationale": "Malformed request body."}, status_code=400)
    body = body if isinstance(body, dict) else {}
    conv_id = str(body.get("conversation_id") or f"conv_adhoc_{uuid.uuid4().hex[:8]}")
    role = str(body.get("from_role") or "merchant").lower()
    msg = str(body.get("message") or "")
    now = body.get("received_at") or now_iso()

    with store.lock:
        conv = store.conversations.get(conv_id)
        if conv is None:
            mid = body.get("merchant_id")
            merchant = store.get("merchant", mid) or {}
            conv = {"merchant_id": mid, "customer_id": body.get("customer_id"), "trigger_id": None, "kind": "",
                    "send_as": "merchant_on_behalf" if role == "customer" else "vera", "role": role, "stage": 0,
                    "offer_action": _history_action(merchant), "slots": [], "turns": [], "created": now}
            store.conversations[conv_id] = conv
        mid = conv.get("merchant_id") or body.get("merchant_id")
        cust_id = conv.get("customer_id") or body.get("customer_id")
        merchant = store.get("merchant", mid) or {"merchant_id": mid, "identity": {}}
        category = store.get("category", merchant.get("category_slug")) or {}
        trigger = store.get("trigger", conv.get("trigger_id")) or {}
        customer = store.get("customer", cust_id) if cust_id else None
        conv.setdefault("turns", []).append({"from": role, "msg": msg, "ts": now})
        f = Facts(category, merchant, trigger, customer, now)
        mstate = store.mstate(mid)
        role_eff = "customer" if (role == "customer" or conv.get("role") == "customer") else "merchant"

    try:
        result = respond(conv, msg, role_eff, f, mstate, llm)
    except Exception as e:
        print(f"[reply] error: {e!r}")
        result = {"action": "wait", "wait_seconds": 1800, "rationale": "Internal error while composing; backing off safely."}

    with store.lock:
        if result.get("action") == "send":
            prev = {t["msg"] for t in conv.get("turns", []) if t.get("from") == "bot"}
            if not result.get("body") or result["body"] in prev:
                result = {"action": "wait", "wait_seconds": 3600,
                          "rationale": "Would have repeated an earlier message verbatim; waiting instead (anti-repetition)."}
            else:
                result.setdefault("cta", "open_ended")
                conv["turns"].append({"from": "bot", "msg": result["body"], "ts": now})
            bot_turns = sum(1 for t in conv["turns"] if t.get("from") == "bot")
            if bot_turns > 6 and result.get("action") == "send":
                result = {"action": "end", "rationale": "Conversation has run long without closing; ending gracefully."}
        if result.get("action") == "end":
            conv["ended"] = True
            if role_eff == "customer" and cust_id and any(c in ("optout", "hostile") for c in conv.get("classes", [])[-1:]):
                store.cstate(cust_id)["opted_out"] = True
        store.mark_dirty()
    store.save_soon()
    return result


# --------------------------------------------------------------------------- teardown

@app.post("/v1/teardown")
def teardown():
    store.wipe()
    return {"ok": True, "wiped_at": now_iso()}


@app.on_event("shutdown")
def _flush():
    store.save(force=True)
