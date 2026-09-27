"""Multi-turn reply engine for /v1/reply.

Flow: classify the incoming message with deterministic rules -> pick an action
(send / wait / end) -> build the body from the conversation's own trigger +
the *latest* contexts in the store. Rules own the decision; the optional LLM
only rewrites free-form answers (questions / objections) and is validated.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .facts import Facts
from .util import (clean_spaces, customer_first, fint, looks_hindi, merchant_hinglish, merchant_name,
                   money, nice_date, num, pct, poss, salutation, short_merchant, stable_hash)

# --------------------------------------------------------------------------- classification

AUTO_PATTERNS = [
    r"thank(s| you) for (contacting|reaching|your message|messaging)",
    r"(we|our team) will (get back|respond|reply|contact)", r"will (get back|respond) (to you )?(shortly|soon|asap)",
    r"\bauto[- ]?(reply|response|generated)\b", r"\bautomated (assistant|message|reply|response)\b",
    r"currently (unavailable|away|closed|not available)", r"(office|business|working) hours",
    r"we have received your (message|query|enquiry)", r"out of (the )?office",
    r"aapki jaankari ke liye", r"hamari team tak", r"jald hi (sampark|jawab)", r"main ek automated",
    r"this is an automated", r"do not reply to this", r"message received",
]
OPTOUT = re.compile(
    r"\b(stop|unsubscribe|opt[- ]?out)\b|don'?t (message|text|contact|send|msg|bother)|do not (message|contact|send)|"
    r"stop (messaging|sending|texting|bothering)|not interested|no interest|remove (me|my number)|block (you|this)|"
    r"mat bhej|band karo|message mat|msg mat|pareshan mat|nahi chahiye kuch|leave me alone", re.I)
HOSTILE = re.compile(
    r"\b(useless|spam|scam|fraud|rubbish|nonsense|bakwas|bekaar|bekar|waste of time|idiot|stupid|shut up|chup|pagal|"
    r"irritating|annoying|bothering me|harass|fake|cheat|bloody|damn|hell)\b|why are you (bothering|messaging|disturbing)", re.I)
ACCEPT = re.compile(
    r"^\s*(yes|yeah|yep|yup|ya|haan|han|haa|ha|ji|ji haan|sure|ok|okay|okk|k|done|confirm|confirmed|go|go ahead|proceed|"
    r"please do|do it|let'?s do it|lets go|let'?s go|chalo|chalega|theek hai|thik hai|kar do|kardo|karo|send|send it|"
    r"bhej do|bhejo|start|interested|agreed|perfect|great|sounds good|good idea|👍|✅)\b"
    r"|\b(let'?s do it|go ahead|please (do|go ahead|proceed|send|start)|i want to (join|do|start|go ahead)|"
    r"judna hai|join karna|shuru karo|kar do|sign me up|book it|book karo|count me in|i'?m in|do it|lets do it|"
    r"yes please|haan ji|haan bhej)\b", re.I)
NEXT_STEP_Q = re.compile(r"what'?s next|whats next|what next|next step|then\?|aage kya|ab kya|how do we (start|proceed)|when can (we|you) start", re.I)
DECLINE_SOFT = re.compile(r"\b(later|not now|busy|baad me|baad mein|abhi nahi|next week|tomorrow|kal baat|call me later|in a meeting|"
                          r"remind me|some other time|not today|thoda time)\b", re.I)
DECLINE = re.compile(r"^\s*(no|nope|nah|nahi|nahin|na|no thanks|no thank you|not needed|don'?t need|nahi chahiye|rehne do|skip)\b", re.I)
OFFTOPIC = re.compile(r"\b(gst|income tax|itr|tax filing|loan|insurance|visa|passport|electricity bill|rent agreement|"
                      r"lawyer|legal notice|court|salary|payroll|cricket score|stock market|share price|mutual fund|"
                      r"laptop|mobile repair|my phone|recharge|aadhaar|pan card)\b", re.I)
OBJECTION = re.compile(r"\b(expensive|costly|too much|mehenga|mahanga|mehnga|no budget|budget nahi|can'?t afford|paisa nahi|"
                       r"didn'?t work|doesn'?t work|does not work|not working|tried (it|this|before)|no time|time nahi|"
                       r"not sure|doubt|why should|what'?s the point|no use|koi fayda|fayda nahi|loss|lose money)\b", re.I)
THANKS = re.compile(r"^\s*(thanks|thank you|thx|ty|shukriya|dhanyavaad|dhanyawad|thanks a lot|great thanks|ok thanks|okay thanks)\b", re.I)
QUESTION = re.compile(r"\?|^\s*(what|how|when|which|who|why|where|can you|could you|is it|are you|will it|does it|do you|kya|kaise|"
                      r"kitna|kitne|kab|kaun|kyun|kahan)\b", re.I)
RESCHEDULE = re.compile(r"\b(reschedule|change|another time|different time|shift|postpone|later slot|evening|morning|"
                        r"weekend|saturday|sunday|monday|tuesday|wednesday|thursday|friday)\b", re.I)


def normalize(msg: str) -> str:
    return re.sub(r"[^a-z0-9ऀ-ॿ ]+", "", (msg or "").lower()).strip()


def is_auto_reply(msg: str, mstate: dict) -> bool:
    t = (msg or "").lower()
    if any(re.search(p, t) for p in AUTO_PATTERNS):
        return True
    n = normalize(msg)
    return len(n) > 25 and mstate.get("auto_replies", {}).get(n, 0) >= 1


def classify(msg: str, role: str, mstate: dict) -> str:
    t = (msg or "").strip()
    if not t:
        return "empty"
    if is_auto_reply(t, mstate):
        return "auto_reply"
    if OPTOUT.search(t):
        return "optout"
    if HOSTILE.search(t):
        return "hostile"
    if role == "customer" and re.fullmatch(r"\s*[1-9]\s*[.)]?\s*", t):
        return "slot_choice"
    if OFFTOPIC.search(t):
        return "offtopic"
    has_q = bool(QUESTION.search(t))
    if ACCEPT.search(t) and (not has_q or NEXT_STEP_Q.search(t)):
        return "accept"
    if NEXT_STEP_Q.search(t):
        return "accept"
    if OBJECTION.search(t):
        return "objection"
    if DECLINE_SOFT.search(t) and not has_q:
        return "decline_soft"
    if DECLINE.search(t):
        return "decline"
    if THANKS.search(t) and not has_q:
        return "thanks"
    if role == "customer" and RESCHEDULE.search(t):
        return "reschedule"
    if has_q:
        return "question"
    return "other"


# --------------------------------------------------------------------------- helpers

def _action_from_body(body: str) -> Optional[str]:
    m = re.search(r"Want me to (.+?)\? Reply", body or "")
    return m.group(1).strip() if m else None


def _hindi_mode(conv: dict, msg: str, merchant: dict) -> bool:
    if looks_hindi(msg):
        return True
    last = [t for t in conv.get("turns", []) if t.get("from") != "bot"]
    if len(last) >= 2 and looks_hindi(last[-2].get("msg", "")):
        return True
    return False


def _wait_seconds(msg: str) -> int:
    t = (msg or "").lower()
    if re.search(r"next week|agle hafte", t):
        return 7 * 86400
    if re.search(r"tomorrow|kal\b", t):
        return 86400
    m = re.search(r"(\d+)\s*(min|minute)", t)
    if m:
        return max(300, int(m.group(1)) * 60)
    m = re.search(r"(\d+)\s*(hr|hour|ghante)", t)
    if m:
        return int(m.group(1)) * 3600
    return 4 * 3600


# --------------------------------------------------------------------------- deliverables (action mode)

def _deliverable(f: Facts, kind: str, action: Optional[str], hi: bool) -> tuple[str, str]:
    """Returns (body, cta) for the first 'yes' — do the work, show it, ask one confirm."""
    name = merchant_name(f.merchant)
    short = short_merchant(f.merchant)
    loc = f.locality
    it = f.digest_for_trigger()
    offer = f.best_active_offer()
    confirm = "Reply CONFIRM and it goes live today." if not hi else "CONFIRM reply karein, aaj hi live kar dungi."
    if kind in ("research_digest", "category_research_digest_release", "research_digest_release") and it:
        lead = str(it.get("summary", "")).split(". ")[0].rstrip(".")
        src = it.get("source", "")
        body = (f"Done — here's the 2-line abstract: {lead} ({src}).\n\n"
                f"Patient WhatsApp draft:\n\"New research ({src}) on {it.get('title', '').split(' — ')[0].lower()}. "
                f"If this applies to you, we'll flag it at your next visit — just reply to book.\"")
        seg = it.get("patient_segment")
        sc = f.segment_count(seg) if seg else None
        tail = (f"Reply CONFIRM and I'll send it to your {fint(sc[1])} matching patients." if sc else
                "Reply CONFIRM and I'll schedule it as a Google post tomorrow 10am.")
        return body + "\n\n" + tail, "binary_confirm_cancel"
    if kind in ("regulation_change", "compliance_alert") and it:
        body = (f"Done — SOP note drafted for {name}:\n• Rule: {it.get('title')}\n• Our setup: ___ (fill: film speed / RVG sensor)\n"
                f"• Checked on: ___ by ___\n• Action if non-compliant: {it.get('actionable') or 'replace before the deadline'}")
        return body + "\n\nReply CONFIRM and I'll save it to your compliance folder and remind you 2 weeks before the deadline.", "binary_confirm_cancel"
    if kind == "cde_opportunity" and it:
        return (f"Done — slot blocked for \"{it.get('title')}\" ({nice_date(it.get('date'), with_day=True)}). "
                f"I'll send the joining details a day before. Reply CONFIRM if you'd like me to add a patient-facing post about digital impressions too."), "binary_confirm_cancel"
    if kind == "supply_alert":
        mol = f.payload.get("molecule", "the recalled product")
        batches = ", ".join(f.payload.get("affected_batches") or [])
        body = (f"On it — filtering your chronic-Rx list for {mol} ({batches}) now.\n\nCustomer WhatsApp draft:\n"
                f"\"Namaste, {name} here. A voluntary recall covers some {mol} batches ({batches}). "
                f"If your strip shows one of these numbers, please bring it in — we'll replace it free. No need to stop your medicine before that.\"")
        return body + "\n\nReply CONFIRM and I'll send it to every matched customer.", "binary_confirm_cancel"
    if kind == "active_planning_intent":
        topic = str(f.payload.get("intent_topic", "")).replace("_", " ")
        if "thali" in topic or "corporate" in topic:
            return (f"Here's the office-admin WhatsApp:\n\"Hi! {name}, {loc} here. We now do corporate lunch thalis — "
                    f"{offer or 'fresh daily thali'}, with bulk pricing from 10 thalis/day and delivery at 12:30-1:30pm. "
                    f"Want a free tasting box for your team this week?\"\n\nReply CONFIRM and I'll set up the bulk-order form + the Google post."), "binary_confirm_cancel"
        return (f"Done — Google post + 5-slide Insta carousel drafted for the {topic} (title, schedule, age band, price, sign-up). "
                f"Reply CONFIRM and I'll publish both today."), "binary_confirm_cancel"
    if kind in ("milestone_reached",):
        return (f"Here's the review request:\n\"Thanks for choosing {name} 🙏 If you enjoyed it, a one-line Google review "
                f"really helps a small business like ours — takes 20 seconds.\"\n\n{confirm}"), "binary_confirm_cancel"
    if kind == "review_theme_emerged":
        theme = str(f.payload.get("theme") or "the issue").replace("_", " ")
        return (f"Draft public reply:\n\"Thank you for the honest feedback — you're right about the {theme}, and we're fixing it this week. "
                f"We'd love the chance to make your next order right.\"\n\nTeam note: \"Recent reviews flag {theme} — "
                f"please double-check timings before dispatch.\"\n\nReply CONFIRM and I'll post the replies."), "binary_confirm_cancel"
    if kind == "winback_eligible":
        return (f"Reactivation request raised. Win-back WhatsApp for your lapsed {f.people}:\n\"Hi! It's been a while — "
                f"{name} misses you. {offer + ' is on this week. ' if offer else ''}Reply to book your slot.\"\n\n"
                f"Reply CONFIRM and I'll send it once the plan is active."), "binary_confirm_cancel"
    if kind == "renewal_due":
        return ("Done — renewal request raised; the payment confirmation will come to this number and nothing on your listing pauses. "
                "Reply CONFIRM once it's through and I'll start the lapsed-customer win-back the same day."), "binary_confirm_cancel"
    if kind == "gbp_unverified":
        return ("Started — Google will send a verification code by postcard or phone call to the number on your listing. "
                "Forward me the code when it arrives (usually within 5 days) and I'll finish the rest."), "open_ended"
    if kind == "curious_ask_due":
        return ("Send me the service name and its price — I'll turn it into the Google post and WhatsApp price reply right away.",
                "open_ended")
    if kind == "dormant_with_vera":
        bp = f.below_peer()
        lines = []
        for m, mv, pv, _ in bp[:2]:
            lines.append(f"• {f.metric_label(m).capitalize()}: {f.metric_value(m, mv)} vs {f.metric_value(m, pv)} peer average")
        from .composer import _diagnose
        cause, fix = _diagnose(f)
        lines.append(f"• Biggest fix: {fix}")
        return ("Here's the 2-minute breakdown:\n" + "\n".join(lines) + "\n\nReply CONFIRM and I'll start with the biggest fix today."), "binary_confirm_cancel"
    act_l = (action or "").lower()
    quoted = re.findall(r"'([^']+)'", action or "")
    if kind == "seasonal_perf_dip":
        members = f.agg.get("total_active_members")
        return (f"Here's the draft:\n\"{short} Summer Consistency Challenge — 6 weeks, 3 check-ins a week. "
                f"Everyone who hits 18 check-ins gets a shout-out on our wall + a free PT session.\"\n\n"
                f"Reply CONFIRM and I'll send it to your {fint(members) + ' ' if members else ''}members and pin it on Google."), "binary_confirm_cancel"
    if kind == "category_seasonal":
        trends = [re.sub(r"_demand_.*", "", str(t)).replace("_", " ") for t in f.payload.get("trends") or [] if "+" in str(t)]
        dl = next((o for o in f.active_offers() if "delivery" in o.lower()), None)
        items = ", ".join(trends[:3]) or "seasonal essentials"
        return (f"WhatsApp draft:\n\"Summer essentials in stock at {name}, {loc}: {items}. "
                f"Order on WhatsApp{' — ' + dl.lower() if dl else ''}.\"\n\n"
                f"Reply CONFIRM and I'll send it to your customer list today."), "binary_confirm_cancel"
    if kind == "ipl_match_today":
        combo = quoted[0] if quoted else "Match-night Combo"
        combo = next((o.get("title").split("(")[0].strip() for o in f.catalog() if "match" in str(o.get("title", "")).lower()), combo)
        return (f"Banner copy:\n\"Watching tonight's match at home? {combo} from {name}, delivered before the toss.\"\n"
                f"Insta story: same line + your best-selling dish photo.\n\n{confirm}"), "binary_confirm_cancel"
    if kind == "competitor_opened":
        pos = f.review_theme("pos")
        proof = f" — {fint(pos.get('occurrences_30d'))} reviews this month mention it" if pos and pos.get("occurrences_30d") else ""
        from .composer import _theme_phrase
        why = _theme_phrase(pos.get("theme")) if pos else "consistent, friendly service"
        return (f"Description line + post draft:\n\"Why {loc} keeps coming back to {name}: {why.replace('your ', 'our ')}{proof}.\"\n\n{confirm}"), "binary_confirm_cancel"
    if kind in ("festival_upcoming", "festival"):
        fest = f.payload.get("festival") or "the festive season"
        base = quoted[0] if quoted else (offer or "our festive specials")
        return (f"Draft post:\n\"{fest} at {name}: {base} — pre-book your slot now and skip the rush.\"\n\n"
                f"Reply CONFIRM and I'll schedule it to go live 6 weeks before."), "binary_confirm_cancel"
    if kind in ("category_trend_movement", "trend_movement") and quoted:
        q = f.payload.get("query") or "this"
        return (f"Draft post:\n\"Looking for {q}? {quoted[0]} at {name}, {loc}.\"\n\n{confirm}"), "binary_confirm_cancel"
    if quoted and any(w in act_l for w in (" live", "feature", "pin ", "set up", "put ")):
        o = quoted[0]
        return (f"Done — listing copy for '{o}':\n\"{o} at {name}, {loc}. Book on WhatsApp or just walk in.\"\n\n{confirm}"), "binary_confirm_cancel"
    if "post" in act_l:
        return (f"Here are 3 post drafts for {name}:\n1) {offer or 'Our most-loved service'} — this week at {loc}.\n"
                f"2) Behind the scenes at {short} — meet the team.\n3) What our {f.people} say — your latest 5★ review.\n\n{confirm}"), "binary_confirm_cancel"
    # generic: use the action we offered
    act = action or "set this up"
    return (f"Great — starting now: I'll {act}. You'll see the draft here within 10 minutes; "
            f"reply CONFIRM once you've checked it and it goes live the same day." if not hi else
            f"Badhiya — shuru kar rahi hoon: {act}. Draft 10 minute mein yahin bhej dungi; dekh kar CONFIRM reply kar dijiye, usi din live ho jayega."), "binary_confirm_cancel"


def _done_msg(f: Facts, kind: str, hi: bool) -> str:
    if hi:
        return "Ho gaya ✅ Live kar diya hai. 7 din baad aapko results (views/calls) yahin bhej dungi."
    return "Done ✅ It's live. I'll send you the first results (views and calls) here in 7 days."


# --------------------------------------------------------------------------- customer-side

def _customer_reply(f: Facts, conv: dict, cls: str, msg: str, hi: bool) -> dict:
    cname, parent = customer_first(f.customer or {})
    slots = conv.get("slots") or []
    mname = merchant_name(f.merchant)
    if cls in ("optout", "hostile"):
        return {"action": "end", "rationale": "Customer opted out / unhappy — stopping all outreach for this customer and conversation."}
    if cls == "auto_reply":
        return {"action": "end", "rationale": "Automated response on the customer's number; no human engagement — closing."}
    if cls == "slot_choice":
        i = int(re.sub(r"\D", "", msg)) - 1
        if 0 <= i < len(slots):
            conv["stage"] = 2
            body = (f"Booked ✅ {slots[i]} at {mname}. Reply CHANGE if anything comes up — see you then!" if not hi else
                    f"Booking pakki ✅ {slots[i]}, {mname}. Kuch badalna ho toh CHANGE reply karein — milte hain!")
            return {"action": "send", "body": body, "cta": "none", "rationale": f"Customer picked slot {i + 1}; confirmed the exact slot label from the payload."}
        cls = "other"
    if cls == "accept":
        if conv.get("stage", 0) >= 2:
            return {"action": "end", "rationale": "Booking/order already confirmed; nothing further needed."}
        conv["stage"] = 2
        kind = conv.get("kind", "")
        if slots and len(slots) > 1:
            conv["stage"] = 1
            body = f"Great! Which one works — reply 1 for {slots[0]} or 2 for {slots[1]}."
            return {"action": "send", "body": body, "cta": "multi_choice_slot", "rationale": "Customer said yes; needs one slot choice to finish booking."}
        if slots:
            body = f"Booked ✅ {slots[0]} at {mname}. Reply CHANGE if you need a different time."
        elif "refill" in kind:
            body = "Dispatch confirmed ✅ Same dose, same brand, to your saved address. We'll message when it's out for delivery." if not hi else \
                "Dispatch pakka ✅ Same dose, same brand, saved address par. Nikalte hi message kar denge."
        elif "appointment" in kind:
            body = f"Confirmed ✅ See you tomorrow at {mname}!" if not hi else f"Confirm ho gaya ✅ Kal milte hain, {mname} par!"
        else:
            pref = (f.customer_prefs() or {}).get("preferred_slots")
            body = (f"Done ✅ We've noted it — {mname} will confirm your {str(pref).replace('_', ' ') + ' ' if pref else ''}slot on this number shortly." if not hi else
                    f"Ho gaya ✅ {mname} aapka slot isi number par jaldi confirm karega.")
        return {"action": "send", "body": body, "cta": "none", "rationale": "Customer accepted — switched straight to confirmation (no further qualifying)."}
    if cls == "reschedule":
        conv["stage"] = 1
        want = [w.capitalize() for w in re.findall(r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend|morning|evening|afternoon", msg.lower())]
        what = (" ".join(dict.fromkeys(want)) + " slot") if want else "different time"
        body = (f"No problem — we'll check a {what} at {mname} and confirm it on this number shortly." if not hi else
                f"Koi baat nahi — {what} check karke isi number par confirm kar dete hain.")
        return {"action": "send", "body": body, "cta": "none", "rationale": "Customer asked for a different time; acknowledged and handed to the merchant to confirm (no invented slot)."}
    if cls in ("decline", "decline_soft"):
        return {"action": "end", "rationale": "Customer declined; closing politely without further nudges."}
    if cls == "thanks":
        return {"action": "end", "rationale": "Customer said thanks; conversation complete."}
    if cls in ("question", "objection", "other", "offtopic"):
        offer = f.best_active_offer()
        t = msg.lower()
        if re.search(r"price|cost|kitna|charge|fee|rate", t) and offer:
            ans = f"{offer} is the current offer at {mname}."
        elif re.search(r"where|address|location|kahan", t):
            ans = f"We're in {f.locality}, {f.city}."
        elif re.search(r"time|timing|open|slot|kab", t) and slots:
            ans = "Open slots: " + " or ".join(slots) + "."
        else:
            ans = f"Good question — the {mname} team will reply to that personally on this number."
        body = ans + (" Reply YES to book." if conv.get("stage", 0) < 2 else "")
        return {"action": "send", "body": body, "cta": "binary_yes_no" if conv.get("stage", 0) < 2 else "none",
                "rationale": "Customer question answered only from merchant context; unknowns routed to the merchant instead of guessed."}
    return {"action": "wait", "wait_seconds": 3600, "rationale": "Unclear customer message; giving them time."}


# --------------------------------------------------------------------------- main entry

def respond(conv: dict, msg: str, role: str, f: Facts, mstate: dict, llm=None) -> dict:
    """conv is mutated (turns, stage, counters). Returns the API response dict."""
    cls = classify(msg, role, mstate)
    hi = _hindi_mode(conv, msg, f.merchant)
    if role == "customer" and f.customer:
        from .util import customer_lang
        hi = hi or customer_lang(f.customer) in ("hi", "hinglish")
    conv.setdefault("classes", []).append(cls)
    kind = conv.get("kind", "")
    sal = salutation(f.merchant, f.slug) if f.merchant else "there"
    action = conv.get("offer_action")

    # --- auto-reply bookkeeping (tracked per merchant, across conversations)
    n = normalize(msg)
    if cls == "auto_reply":
        mstate["auto_replies"][n] = mstate["auto_replies"].get(n, 0) + 1
        mstate["auto_total"] = mstate.get("auto_total", 0) + 1
        conv["auto_count"] = conv.get("auto_count", 0) + 1
        count = max(mstate["auto_total"], conv["auto_count"])
        if role == "customer":
            return _customer_reply(f, conv, cls, msg, hi)
        if count == 1:
            what = f"I'll {action}" if action else "I'll pick up right where we left off"
            body = (f"Looks like an auto-reply 🙂 {sal}, whenever you see this, just reply YES and {what}." if not hi else
                    f"Lagta hai yeh auto-reply hai 🙂 {sal}, jab aap dekhein, bas YES reply kar dijiye — baaki main kar dungi.")
            return {"action": "send", "body": body, "cta": "binary_yes_no",
                    "rationale": "Detected WhatsApp Business auto-reply (canned phrasing). One short owner-flag nudge, no pitch repeat."}
        if count == 2:
            return {"action": "wait", "wait_seconds": 86400,
                    "rationale": "Same auto-reply again — owner isn't at the phone. Backing off 24h instead of burning turns."}
        conv["ended"] = True
        return {"action": "end", "rationale": f"Auto-reply {count}x with no human response; closing to avoid spamming the merchant."}
    else:
        # a real human reply resets the auto-reply streak
        mstate["auto_total"] = 0
        if len(n) > 25:
            mstate["auto_replies"][n] = mstate["auto_replies"].get(n, 0) + 1

    if role == "customer":
        return _customer_reply(f, conv, cls, msg, hi)

    # --- re-open after an end only on genuine re-engagement
    if conv.get("ended") and cls not in ("accept", "question"):
        return {"action": "end", "rationale": "Conversation already closed; not re-engaging without a clear merchant request."}
    if conv.get("ended"):
        conv["ended"] = False
        mstate["opted_out"] = False

    if cls == "empty":
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Empty message; waiting for the merchant."}

    if cls == "optout":
        conv["ended"] = True
        mstate["opted_out"] = True
        return {"action": "end", "rationale": "Merchant explicitly asked to stop. Closing and suppressing further proactive sends to this merchant."}

    if cls == "hostile":
        if conv.get("hostile_seen"):
            conv["ended"] = True
            mstate["opted_out"] = True
            return {"action": "end", "rationale": "Second frustrated message — exiting gracefully and suppressing further sends."}
        conv["hostile_seen"] = True
        body = (f"Sorry for the bother, {sal} — I won't push. If you ever want help with {short_merchant(f.merchant)}'s listing, just reply 'Hi Vera'. 🙏"
                if not hi else f"Maaf kijiye, {sal} — main aur pareshan nahi karungi. Kabhi listing mein madad chahiye ho toh bas 'Hi Vera' likh dijiye. 🙏")
        return {"action": "send", "body": body, "cta": "none",
                "rationale": "Merchant frustrated (no explicit opt-out): one-line apology + easy way back, no pitch. Will end on any further frustration."}

    if cls == "offtopic":
        topic = OFFTOPIC.search(msg).group(0)
        back = f"Coming back to what I can do today — want me to {action}? Reply YES." if action else \
            f"What I can do today is keep {poss(short_merchant(f.merchant))} Google listing sharp — reply YES and I'll send the 2-minute health check."
        body = (f"{topic.upper() if len(topic) <= 4 else topic.capitalize()} is outside what I can help with — your CA / the right expert is best for that. " + back
                if not hi else f"{topic.upper() if len(topic) <= 4 else topic.capitalize()} mein main madad nahi kar paungi — uske liye aapke CA sahi rahenge. " + back)
        return {"action": "send", "body": body, "cta": "binary_yes_no",
                "rationale": "Out-of-scope request politely declined; redirected to the open thread with one CTA."}

    if cls == "accept":
        stage = conv.get("stage", 0)
        if stage == 0:
            conv["stage"] = 1
            body, cta = _deliverable(f, kind, action, hi)
            return {"action": "send", "body": body, "cta": cta,
                    "rationale": "Merchant committed — switched from pitch to action mode immediately: delivered the artifact and a single confirm step (no more qualifying)."}
        if stage == 1:
            conv["stage"] = 2
            return {"action": "send", "body": _done_msg(f, kind, hi), "cta": "none",
                    "rationale": "Merchant confirmed; executed and closed the loop with a concrete follow-up promise."}
        conv["ended"] = True
        return {"action": "end", "rationale": "Task already completed; nothing further needed — ending cleanly."}

    if cls == "decline_soft":
        return {"action": "wait", "wait_seconds": _wait_seconds(msg),
                "rationale": "Merchant asked for time; backing off rather than pushing."}

    if cls == "decline":
        conv["ended"] = True
        return {"action": "end", "rationale": "Merchant declined; closing politely without further nudges on this topic."}

    if cls == "thanks":
        if conv.get("stage", 0) >= 1:
            conv["ended"] = True
            return {"action": "end", "rationale": "Merchant thanked after the work was delivered — conversation complete."}
        cls = "other"

    # curious-ask: any non-question answer IS the answer we asked for -> deliver
    if kind in ("curious_ask_due", "scheduled_recurring") and cls == "other":
        conv["stage"] = 1
        ans = re.sub(r"\b(mostly|probably|i think|i guess|it'?s|its|it is|definitely|for sure|this week|sabse zyada|zyada|"
                     r"mainly|currently|right now|the)\b", " ", msg, flags=re.I)
        ans = re.sub(r"\s+", " ", ans).strip(" .!,-")[:60] or msg.strip()[:60]
        ans = ans[:1].upper() + ans[1:]
        body = (f"Perfect — \"{ans}\" it is. Google post draft:\n\"{ans} at {merchant_name(f.merchant)}, {f.locality} — "
                f"book on WhatsApp or walk in today.\"\n\nWhatsApp price reply: \"Thanks for asking! {ans} is ₹[your price] at {short_merchant(f.merchant)} — "
                f"reply with a time and we'll hold a slot.\"\n\nReply CONFIRM with your price and I'll post both.")
        return {"action": "send", "body": body, "cta": "binary_confirm_cancel",
                "rationale": "Merchant answered the curious-ask; delivered the promised Google post + WhatsApp reply immediately."}

    # questions / objections / other: try LLM (validated), else template
    if llm is not None and getattr(llm, "enabled", False):
        out = llm.reply(f, conv, msg, cls, action, hi)
        if out:
            return {"action": "send", "body": out, "cta": "binary_yes_no",
                    "rationale": f"{cls}: answered from merchant/trigger context (LLM, validated against context facts); one CTA back to the open thread."}

    body = _template_answer(f, conv, msg, cls, action, hi, sal)
    return {"action": "send", "body": body, "cta": "binary_yes_no",
            "rationale": f"{cls}: answered only from known context (no guessing), then one CTA back to the open thread."}


def _template_answer(f: Facts, conv: dict, msg: str, cls: str, action: Optional[str], hi: bool, sal: str) -> str:
    t = msg.lower()
    name = merchant_name(f.merchant)
    act = action or "set this up for you"
    cta = f"Want me to go ahead and {act}? Reply YES." if not hi else f"Main {act} kar doon? YES reply kijiye."
    if cls == "objection":
        if re.search(r"expensive|costly|mehenga|mahanga|mehnga|budget|afford|paisa", t):
            amt = f.payload.get("renewal_amount")
            v, c = f.perf.get("views"), f.perf.get("calls")
            if amt and v:
                lead = f"Fair question. For context, your listing brought {fint(v)} views and {fint(c)} calls in the last 30 days against a {money(amt)} plan."
            else:
                lead = "Fair point — this is a draft, not a spend: nothing goes live (and no discount runs) without your OK."
        elif re.search(r"tried|didn'?t work|doesn'?t work|not working|no use|fayda", t):
            bp = f.below_peer()
            lead = ("Fair — what didn't work before is worth knowing. " +
                    (f"One thing is measurably different now: your {f.metric_label(bp[0][0])} is {f.metric_value(bp[0][0], bp[0][1])} vs {f.metric_value(bp[0][0], bp[0][2])} for peers, so there's clear room." if bp else
                     "This time it's a small, measurable test — I'll report the numbers back in 7 days."))
        elif re.search(r"no time|time nahi|busy", t):
            lead = "Totally get it — that's exactly why I do the work. It takes you 30 seconds to approve."
        else:
            lead = "Fair point. Let's keep it small: a draft first, nothing goes live until you say so."
        return clean_spaces(f"{lead} {cta}")
    if cls == "question":
        if re.search(r"who are you|kaun ho|are you (a )?(bot|human|real)", t):
            return f"I'm Vera, magicpin's assistant for {name} — I handle your listing, posts and customer messages so you don't have to. {cta}"
        if re.search(r"how (does|will|do) (it|this|that) work|kaise|how (it|this) works|process", t):
            return clean_spaces(f"Simple: 1) I draft it, 2) you approve here on WhatsApp, 3) it goes live on Google / to your list the same day. {cta}")
        if re.search(r"price|cost|charge|fee|kitna|how much", t):
            amt = f.payload.get("renewal_amount")
            offer = f.best_active_offer()
            if amt:
                return f"The plan renewal is {money(amt)}. Drafting posts and messages is included. {cta}"
            if offer:
                return f"Your live offer stays {offer} — drafting and posting it is part of what I do for your listing. {cta}"
            return f"Drafting and posting is part of what I do for your listing. {cta}"
        if re.search(r"how many|kitne|which customers|who (will|all)|list", t):
            agg = f.agg
            bits = [f"{fint(v)} {k.replace('_', ' ').replace('pct', '%')}" for k, v in agg.items() if num(v) and "pct" not in k][:2]
            if bits:
                return clean_spaces(f"From your data: {', '.join(bits)}. {cta}")
        if re.search(r"when|kab|how long|kitne din", t):
            return f"Same day — the draft comes to you within 10 minutes and goes live as soon as you approve. {cta}"
        if re.search(r"why|kyun", t):
            cause, fix = __import__("vera.composer", fromlist=["_diagnose"])._diagnose(f)
            return clean_spaces(f"Because {cause[0].lower() + cause[1:]}. {cta}")
        return f"Good question — let me confirm the exact details and come back to you here. Meanwhile, {cta[0].lower() + cta[1:]}"
    # other
    return clean_spaces(f"Got it, {sal}. {cta}") if not hi else clean_spaces(f"Samajh gayi, {sal}. {cta}")
