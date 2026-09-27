"""Deterministic message composer.

compose(category, merchant, trigger, customer=None, now=None) -> dict

Design rules (straight from the brief + case studies):
  * Pick ONE signal that explains "why now"; never dump every fact.
  * Every number / name / date comes from the contexts (or simple arithmetic on them).
  * Service+price offers beat "% off"; category catalog offers are only ever *suggested*.
  * One CTA, placed last. No URLs. No taboo vocabulary. No re-introductions.
  * Customer-facing sends go out as merchant_on_behalf and require recorded consent.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .facts import Facts
from .util import (REGIONAL_GREETING, clean_spaces, ctr_str, customer_first, customer_lang,
                   days_between, fint, first_sentence, humanize, merchant_hinglish, merchant_name,
                   money, nice_date, nice_time, num, owner_first, parse_date, pct, pick, poss,
                   pretty_pref, salutation, short_merchant)

# --------------------------------------------------------------------------- result helpers


class Msg:
    def __init__(self, body: str, cta: str, rationale: str, params: Optional[list] = None,
                 template: Optional[str] = None):
        self.body = clean_spaces(body)
        self.cta = cta
        self.rationale = rationale
        self.params = params or []
        self.template = template


def _window(w: Any) -> str:
    w = str(w or "7d").lower()
    m = re.match(r"(\d+)\s*d", w)
    if m:
        n = int(m.group(1))
        return "week" if n == 7 else f"{n} days"
    return humanize(w)


HINGLISH_TAILS = [" — baaki main sambhal loongi", ", main 10 minute mein ready kar dungi",
                  " — aapko bas itna hi karna hai"]


def _ask(f: Facts, action: str, reply: str = "YES") -> str:
    """Single binary CTA, always the last sentence. Light Hinglish tail for Hindi-comfortable,
    non-clinical merchants (dentists keep the peer-clinical English register)."""
    tail = ""
    if merchant_hinglish(f.merchant) and f.slug != "dentists":
        tail = pick(HINGLISH_TAILS, f"{f.merchant.get('merchant_id')}|{f.trigger.get('kind')}")
    return f"Want me to {action}? Reply {reply}{tail}."


ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _nice_iso(text: str) -> str:
    return ISO_DATE.sub(lambda m: nice_date(m.group(1), with_year=True), text or "")


def _seg(seg: Any) -> str:
    return humanize(seg).replace("high risk", "high-risk").replace("low risk", "low-risk")


THEME_PHRASE = {
    "doctor_manner": "how patiently you explain treatment", "thali_quality": "your thali",
    "stylist_skill": "your stylists", "instructor_quality": "your instructors", "delivery_speed": "your delivery speed",
    "equipment_quality": "your equipment", "pizza_quality": "your pizza", "medicine_availability": "your stock availability",
    "small_classes": "your small class sizes",
}


def _theme_phrase(theme: Any) -> str:
    t = str(theme or "")
    return THEME_PHRASE.get(t, "your " + humanize(t).replace(" quality", ""))


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _strip_period(s: str) -> str:
    return (s or "").strip().rstrip(".")


def _item_line(it: dict) -> str:
    """One clean sentence describing a digest item using only its own fields."""
    title = _strip_period(str(it.get("title", "")))
    return title


def _source(it: dict) -> str:
    return str(it.get("source") or "").strip()


# --------------------------------------------------------------------------- merchant-facing handlers


def h_research(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    it = f.digest_for_trigger() or f.pick_digest(["research", "trend", "tech", "supply", "seasonal", "cde", "compliance"])
    if not it:
        return h_generic(f)
    src = _source(it)
    summary = str(it.get("summary", ""))
    lead = first_sentence(summary)
    n = num(it.get("trial_n"))
    seg = it.get("patient_segment")
    rel = ""
    if seg:
        sc = f.segment_count(seg)
        if sc:
            rel = f"You have {fint(sc[1])} {_seg(seg).replace('adults', 'adult patients')} on your roster, so this maps straight onto your recall schedule."
        else:
            rel = f"Relevant if your case-mix includes {_seg(seg)}."
    elif it.get("actionable"):
        rel = "Takeaway: " + _strip_period(str(it.get("actionable")))[0].lower() + _strip_period(str(it.get("actionable")))[1:] + "."
    kind = str(it.get("kind", "research"))
    what = {"research": "research item", "trend": "trend", "tech": "product note",
            "supply": "supply update", "seasonal": "seasonal read", "cde": "CDE item",
            "compliance": "compliance note", "alert": "alert"}.get(kind, "item")
    head = f"{sal}, one {what} from this week's digest worth 2 minutes — {_item_line(it)}"
    if src:
        head += f" ({src})"
    body_parts = [head + "."]
    if n:
        body_parts.append(f"{lead.rstrip('.')} (n={fint(n)}).")
    elif lead and lead.lower() not in head.lower():
        body_parts.append(lead)
    if rel:
        body_parts.append(rel)
    if f.slug == "dentists":
        action = "pull the abstract and draft a patient WhatsApp you can forward"
    elif f.slug == "pharmacies":
        action = "turn this into a short counter note + a WhatsApp for your repeat customers"
    elif f.slug == "gyms":
        action = "turn this into a 4-line post for your members"
    elif f.slug == "salons":
        action = "draft a Google post + client WhatsApp around it"
    else:
        action = "draft a Google post around it for this week"
    body_parts.append(_ask(f, action))
    body = " ".join(body_parts)
    rat = (f"{f.trigger.get('kind')} trigger: surfaced digest item '{it.get('id')}' ({kind}) with its source"
           f"{' and trial size' if n else ''}; tied it to {'the merchant cohort count' if rel.startswith('You have') else 'the merchant case-mix'}; "
           f"single low-effort CTA (reciprocity: Vera drafts the artifact).")
    return Msg(body, "binary_yes_no", rat, [sal, _item_line(it), action])


def h_regulation(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    it = f.digest_for_trigger() or f.pick_digest(["compliance", "alert"])
    deadline = f.payload.get("deadline_iso") or f.payload.get("deadline") or f.payload.get("effective_date")
    if not it:
        if not deadline:
            return h_generic(f)
        it = {"title": humanize(f.payload.get("topic") or "a regulatory change"), "source": "", "summary": ""}
    src = _nice_iso(_source(it))
    title = _nice_iso(_item_line(it))
    parts = [f"{sal}, compliance heads-up: {title}" + (f" ({src})." if src else ".")]
    if it.get("summary"):
        parts.append(_nice_iso(str(it.get("summary")).strip()))
    if deadline and nice_date(deadline, with_year=True) not in " ".join(parts):
        parts.append(f"Deadline: {nice_date(deadline, with_year=True)}.")
    act = str(it.get("actionable") or "").strip()
    if act:
        parts.append(f"Suggested step: {act[0].lower() + act[1:]}" + ("" if act.endswith(".") else "."))
    blob = f"{it.get('title', '')} {it.get('summary', '')}".lower()
    if re.search(r"radiograph|x-ray|iopa|rvg", blob):
        action = "draft the one-page SOP note documenting your current X-ray setup"
    elif f.slug == "pharmacies" and re.search(r"register|schedule h|dispens", blob):
        action = "draft a checklist for your register audit"
    elif f.slug in ("dentists", "pharmacies"):
        action = "draft the one-page SOP note + a checklist for your team"
    elif f.slug == "restaurants":
        action = "draft a quick checklist for your team"
    else:
        action = "draft a one-page checklist for this"
    parts.append(_ask(f, action))
    rat = (f"regulation_change: compliance item '{it.get('id', 'n/a')}' with source + deadline; "
           f"urgency {f.trigger.get('urgency')} so it leads with the rule and the date, then one concrete prep step.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, _item_line(it), action])


def h_cde(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    it = f.digest_for_trigger() or f.pick_digest(["cde"])
    if not it:
        return h_generic(f)
    date_s = it.get("date") or f.payload.get("date")
    when = ""
    if date_s:
        when = nice_date(date_s, with_day=True)
        t = nice_time(date_s)
        if t:
            when += f", {t}"
    credits = f.payload.get("credits") or it.get("credits")
    fee = str(it.get("actionable") or "").strip()
    fee_bits = []
    if credits:
        fee_bits.append(f"{credits} CDE credits")
    if re.search(r"free|₹", fee, re.I):
        fee_bits.append(_strip_period(fee[0].lower() + fee[1:]))
    elif f.payload.get("fee"):
        fee_bits.append(humanize(f.payload.get("fee")))
    parts = [f"{sal}, a CDE session worth blocking: {_item_line(it)}" + (f" — {when}" if when else "") + "."]
    if fee_bits:
        parts[-1] = parts[-1][:-1] + " (" + "; ".join(fee_bits) + ")."
    if it.get("summary"):
        parts.append(str(it.get("summary")).strip())
    src = _source(it)
    if src:
        parts.append(f"Source: {src}.")
    parts.append(_ask(f, "register you and block the slot on your calendar"))
    rat = (f"cde_opportunity: event '{it.get('id')}' with date, credits and fee from the digest; "
           f"low-urgency learning nudge, so a single effort-externalising CTA (Vera registers).")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, _item_line(it), when])


def h_competitor(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    name = p.get("competitor_name")
    dist = p.get("distance_km")
    their = p.get("their_offer")
    opened = p.get("opened_date")
    loc = f.locality
    if name:
        s = f"{sal}, a new {f.noun} — {name} — opened"
        if dist:
            s += f" {dist} km from you"
        if opened:
            s += f" on {nice_date(opened)}"
        s += "."
    else:
        s = f"{sal}, a new {f.noun} has just opened near you in {loc}." if loc else f"{sal}, a new {f.noun} has just opened near you."
    parts = [s]
    mine = f.price_active_offer()
    if their:
        line = f"They're advertising {their}"
        tp = num(re.sub(r"[^\d.]", "", str(their).split("@")[-1])) if "@" in str(their) else None
        mp = num(re.sub(r"[^\d.]", "", mine.split("@")[-1])) if mine and "@" in mine else None
        if tp and mp and mp > tp:
            line += f" — {money(mp - tp)} under your {mine.split('@')[-1].strip()}"
        parts.append(line + ".")
    strength = f.review_theme("pos")
    if strength and num(strength.get("occurrences_30d")):
        q = strength.get("common_quote")
        edge = (f"I wouldn't match them on price — your edge is {_theme_phrase(strength.get('theme'))}: "
                f"{fint(strength.get('occurrences_30d'))} reviews praised it this month")
        if q:
            edge += f" (\"{q}\")"
        parts.append(edge + ".")
        action = f"put that into your Google description and a post this week"
    else:
        rep = num(f.agg.get("repeat_customer_pct")) or num(f.agg.get("retention_6mo_pct")) or num(f.agg.get("retention_3mo_pct"))
        if rep:
            parts.append(f"Your {pct(rep)} repeat/retention rate is the moat here — regulars rarely switch for a new listing.")
            action = "draft a thank-you WhatsApp to your regulars this week"
        else:
            gap = f.above_peer()
            if gap:
                m, mv, pv, _ = gap[0]
                parts.append(f"You already out-pull the local average on {f.metric_label(m)} ({f.metric_value(m, mv)} vs {f.metric_value(m, pv)}), so the aim is to hold attention, not cut prices.")
            else:
                parts.append("New listings usually run launch offers for a few weeks — the cheapest defence is a fresh, active profile.")
            action = "refresh your profile with 3 new posts this week"
    parts.append(_ask(f, action))
    rat = ("competitor_opened: " + (f"named competitor {name} ({dist} km, {their}) from payload" if name else "payload had no competitor details, so none were invented")
           + "; recommendation leans on the merchant's own strength instead of a price war; one CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, name or f"new {f.noun}", action])


def _diagnose(f: Facts) -> tuple[str, str]:
    """(cause sentence, fix action) — first applicable cause wins."""
    sig = f.signals()
    offer_sugg = f.suggested_catalog_offer()
    if not f.active_offers() and offer_sugg:
        return ("There's no live offer on your listing, so searchers have nothing specific to act on",
                f"put '{offer_sugg}' live on your profile today")
    if f.ident.get("verified") is False or "unverified_gbp" in sig:
        return ("your Google profile is still unverified, which limits how often you appear in local search",
                "start Google verification for you (one phone call or a postcard)")
    if "no_recent_post" in sig:
        return ("you haven't put up a Google post recently, so the listing looks inactive to searchers",
                "draft 3 fresh posts for your review")
    if "stale_posts" in sig:
        age = sig.get("stale_posts")
        return (f"your last Google post was {age.replace('d', ' days')} ago" if age else "your Google posts have gone stale",
                "draft 3 fresh posts for your review")
    g = f.peer_gap("ctr")
    if g and g[0] < g[1]:
        return (f"only {ctr_str(g[0])} of people who see you click through (peer {f.noun}s: {ctr_str(g[1])})",
                "rework your top photo and description to lift clicks")
    best = f.best_active_offer()
    if best:
        return (f"your '{best}' offer isn't being pushed anywhere right now",
                f"feature '{best}' in a Google post this week")
    return ("your listing hasn't had fresh activity recently", "draft 3 fresh posts for your review")


def h_perf_dip(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    name = merchant_name(f.merchant)
    p = f.payload
    metric, d = p.get("metric"), num(p.get("delta_pct"))
    window, base = p.get("window"), p.get("vs_baseline")
    src = "payload"
    if not metric or d is None:
        wd = f.worst_delta()
        if wd:
            metric, d = wd
            window, base, src = "7d", None, "delta_7d"
    parts = []
    if metric and d is not None and d < 0:
        s = f"{sal}, {f.metric_label(metric)} for {name} are down {pct(d)} this {_window(window)}" if _window(window) == "week" \
            else f"{sal}, {f.metric_label(metric)} for {name} are down {pct(d)} over the last {_window(window)}"
        if base:
            s += f" (your usual baseline: {fint(base)})"
        parts.append(s + ".")
        signal_txt = f"{metric} {pct(d, True)}"
    else:
        bp = f.below_peer()
        if bp:
            m, mv, pv, _ = bp[0]
            parts.append(f"{sal}, {name} is trailing nearby {f.noun}s on {f.metric_label(m)}: {f.metric_value(m, mv)} in the last 30 days vs a peer average of {f.metric_value(m, pv)}.")
            signal_txt = f"{m} below peer"
        else:
            v = f.perf.get("views")
            parts.append(f"{sal}, momentum on {name}'s listing has flattened — {fint(v)} views in 30 days." if v else f"{sal}, momentum on {name}'s listing has flattened this week.")
            signal_txt = "flat momentum"
    seasonal = next((k for k in f.signals() if k.startswith("seasonal_dip")), None)
    if seasonal:
        beat = f.current_beat()
        parts.append("Part of this is the expected seasonal dip for " + f.slug
                     + (f" ({beat.get('month_range')}: \"{_strip_period(beat.get('note'))}\")" if beat else "")
                     + " — but not all of it.")
    cause, fix = _diagnose(f)
    g = f.peer_gap("ctr")
    extra = ""
    if g and g[0] < g[1] and "click" not in cause:
        extra = f" Your click-through rate is also {ctr_str(g[0])} vs {ctr_str(g[1])} for peers."
    parts.append(f"Most likely reason: {cause[0].lower() + cause[1:]}.{extra}")
    parts.append(_ask(f, fix))
    rat = (f"perf_dip: signal = {signal_txt} (source: {src}); diagnosed a single most-likely cause from merchant state "
           f"and proposed one fix. Loss-aversion framing, binary CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, signal_txt, fix])


def h_seasonal_dip(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    metric, d = p.get("metric") or "views", num(p.get("delta_pct"))
    if d is None:
        wd = f.worst_delta()
        if wd:
            metric, d = wd
    beat = f.current_beat() or f.seasonal_beat(["low", "lull", "slow", "drop"])
    dig = None
    for it in f.category.get("digest") or []:
        if it.get("kind") == "seasonal":
            dig = it
            break
    parts = []
    if d is not None:
        parts.append(f"{sal}, your {f.metric_label(metric)} are down {pct(d)} this week — before you worry, this is the expected seasonal pattern, not a {f.noun} problem.")
    else:
        parts.append(f"{sal}, a seasonal slowdown is showing on your listing — expected for this time of year, not a {f.noun} problem.")
    if beat:
        parts.append(f"{beat.get('month_range')} is typically \"{_strip_period(beat.get('note'))}\" for {f.slug}.")
    if dig and dig.get("actionable"):
        parts.append(f"The smarter move: {dig.get('actionable')[0].lower() + _strip_period(dig.get('actionable'))[1:]}.")
    members = num(f.agg.get("total_active_members"))
    churn = num(f.agg.get("monthly_churn_pct"))
    peer_churn = num(f.peer.get("monthly_churn_pct"))
    if members:
        s = f"Meanwhile, protect the {fint(members)} members you already have"
        if churn and peer_churn:
            s += f" — churn is {pct(churn)}/month vs a {pct(peer_churn)} peer average"
        parts.append(s + ".")
        action = "draft a 6-week summer consistency challenge for your members"
    else:
        action = f"draft a retention message for your existing {f.people}"
    parts.append(_ask(f, action))
    rat = ("seasonal_perf_dip flagged as expected: reframed the dip (anxiety pre-emption) using the category seasonal beat "
           "+ digest guidance, redirected effort to retention of the merchant's own base; one CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, f"{metric} {pct(d or 0, True)}", action])


def h_perf_spike(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    metric, d = p.get("metric"), num(p.get("delta_pct"))
    base, driver, window = p.get("vs_baseline"), p.get("likely_driver"), p.get("window")
    if not metric or d is None:
        bd = f.best_delta()
        if bd:
            metric, d = bd
            window = "7d"
    name = merchant_name(f.merchant)
    parts = []
    if metric and d is not None and d > 0:
        s = f"{sal}, good news — {f.metric_label(metric)} for {name} are up {pct(d)} this {_window(window)}"
        if base:
            s += f" (vs your usual {fint(base)})"
        parts.append(s + ".")
    else:
        ab = f.above_peer()
        if ab:
            m, mv, pv, _ = ab[0]
            parts.append(f"{sal}, {name} is outperforming nearby {f.noun}s on {f.metric_label(m)}: {f.metric_value(m, mv)} vs a peer average of {f.metric_value(m, pv)}.")
            metric = m
        else:
            parts.append(f"{sal}, {name}'s listing is picking up this week.")
    offer = f.best_active_offer()
    if driver:
        parts.append(f"The lift lines up with your {humanize(driver)} — that demand is showing up before it's even a bookable offer.")
        action = f"turn that {humanize(driver)} into a bookable offer on your profile so these callers can sign up directly"
    elif offer:
        parts.append(f"Best time to put '{offer}' in front of this extra traffic — interest is already warm.")
        action = f"pin '{offer}' as a Google post while the traffic is up"
    else:
        sugg = f.suggested_catalog_offer()
        parts.append(f"Right now there's no live offer to catch this traffic — people are looking, but have nothing specific to act on.")
        action = f"put '{sugg}' live while the traffic is up" if sugg else "draft a post to catch this traffic"
    parts.append(_ask(f, action))
    rat = (f"perf_spike: {metric} {pct(d or 0, True)} — momentum framing; converts extra attention via "
           f"{'the likely driver from payload' if driver else 'the merchant offer' if offer else 'a suggested catalog offer (none live)'}; one CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, f"{metric} up", action])


def h_renewal(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    days = num(p.get("days_remaining", f.sub.get("days_remaining")))
    plan = p.get("plan") or f.sub.get("plan") or "magicpin"
    amt = p.get("renewal_amount")
    status = str(f.sub.get("status", "")).lower()
    name = merchant_name(f.merchant)
    v, c = f.perf.get("views"), f.perf.get("calls")
    value = f"{fint(v)} profile views and {fint(c)} calls" if v is not None and c is not None else None
    lapsed = num(f.agg.get("lapsed_180d_plus")) or num(f.agg.get("lapsed_90d_plus"))
    parts = []
    if status == "expired" and not days:
        since = f.sub.get("days_since_expiry")
        parts.append(f"{sal}, {poss(name)} {plan} plan lapsed{f' {fint(since)} days ago' if since else ''}, so profile upkeep is paused.")
        if value:
            parts.append(f"Even without it, the listing pulled {value} in the last 30 days — that's demand going unmanaged.")
        action, mode = "reactivate the plan today", "lapsed"
    elif days is not None and days <= 30:
        word = "trial ends" if status == "trial" or str(plan).lower() == "trial" else f"{plan} plan renews"
        parts.append(f"{sal}, {poss(name)} {word} in {fint(days)} days.")
        if value:
            parts.append(f"In the last 30 days the listing brought you {value}.")
        if lapsed:
            parts.append(f"You also have {fint(lapsed)} lapsed {f.people} I can start winning back once the plan is active.")
        if amt:
            parts.append(f"Renewal is {money(amt)}.")
        action = "convert the trial to a full plan so nothing pauses" if "trial" in word else "process the renewal so nothing pauses"
        mode = "due"
    else:
        parts.append(f"{sal}, quick plan check-in: {poss(name)} {plan} plan has {fint(days)} days left, so nothing is due yet." if days
                     else f"{sal}, quick plan check-in on {name}.")
        if value:
            parts.append(f"This month it delivered {value}.")
        cause, fix = _diagnose(f)
        parts.append(f"One thing that would get more out of it before renewal comes round: {cause[0].lower() + cause[1:]}.")
        action, mode = fix, "early"
    parts.append(_ask(f, action))
    rat = (f"renewal_due ({mode}): {fint(days) if days is not None else 'n/a'} days left on {plan}. "
           + ("Plan is far from expiry, so no premature renewal pitch — used the moment to add value with one fix. " if mode == "early" else
              "Value anchor = merchant's own 30-day views/calls; loss-aversion without pressure. ")
           + "One CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, str(days), plan])


FESTIVE_KEYWORDS = ["festival", "diwali", "wedding", "christmas", "new year", "holi", "valentine"]


def h_festival(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    fest = p.get("festival") or p.get("name")
    date_s = p.get("date")
    days = num(p.get("days_until"))
    if days is None and date_s and f.now:
        days = days_between(f.now, date_s)
    beat = f.seasonal_beat(FESTIVE_KEYWORDS)
    parts = []
    if fest:
        s = f"{sal}, {fest} is on {nice_date(date_s)}" if date_s else f"{sal}, {fest} is coming up"
        if days is not None:
            s += f" — {fint(days)} days away"
        parts.append(s + ".")
        if beat:
            parts.append(f"For {f.slug}, {beat.get('month_range')} is \"{_strip_period(beat.get('note'))}\".")
    elif beat:
        from .util import month_of, months_until_range
        mu = months_until_range(str(beat.get("month_range")), month_of(f.now))
        if days is None and mu is not None:
            days = mu * 30 if mu else 0
        when = "is on now" if mu == 0 else f"opens in about {mu} month{'s' if mu != 1 else ''}"
        parts.append(f"{sal}, the festive window for {f.slug} {when}: {beat.get('month_range')} — \"{_strip_period(beat.get('note'))}\".")
    else:
        parts.append(f"{sal}, the festive season is coming up.")
    offer = f.premium_active_offer()
    sugg = f.suggested_catalog_offer("festive")
    if days is not None and days > 60:
        if f.slug == "pharmacies":
            parts.append("Too early to act on the shelf — but the pharmacies that catch this demand are the ones stocked and visible before it starts.")
            base = f"'{offer}'" if offer else (f"'{sugg}'" if sugg else "your festive essentials")
            action = f"draft a pre-season post around {base}, ready to go live 6 weeks out"
        else:
            parts.append(f"Too early for a discount push — but festive bookings get decided early, and the {f.nouns} that fill up are the ones that open pre-bookings first.")
            base = f"'{offer}'" if offer else (f"a '{sugg}' package" if sugg else "your best service")
            action = f"draft a festive pre-booking post built on {base}, ready to go live 6 weeks out"
    else:
        if offer:
            parts.append(f"This is the window to put '{offer}' in front of festive searchers.")
            action = f"draft a festive Google post + WhatsApp featuring '{offer}'"
        else:
            parts.append("There's no live offer on your listing yet — a clear service+price package converts best in festive weeks" + (f", e.g. '{sugg}'." if sugg else "."))
            action = f"set up '{sugg}' as your festive offer" if sugg else "draft a festive package post for your listing"
    parts.append(_ask(f, action))
    rat = (f"festival_upcoming: {fest or 'festive window (payload had no festival name; used the category seasonal beat instead of inventing one)'}"
           f"{f', ~{int(days)} days out' if days is not None else ''} — timing judgment ({'plan-ahead, no premature promo' if days is not None and days > 60 else 'launch window'}); one CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, fest or "festive season", action])


def h_curious(f: Facts) -> Msg:
    first = salutation(f.merchant, f.slug)
    short = short_merchant(f.merchant)
    q = {
        "salons": f"which service have clients asked about most at {short} this week?",
        "restaurants": f"which dish has been ordered most at {short} this week?",
        "gyms": f"what are new walk-ins at {short} asking about most this week — weight loss, strength or classes?",
        "pharmacies": f"which medicine or product have customers asked for most at {short} this week?",
        "dentists": "which treatment have patients asked about most this week?",
    }.get(f.slug, f"what are customers asking for most at {short} this week?")
    parts = [f"Hi {first}, quick one — {q}"]
    vocab = [str(v).lower() for v in ((f.category.get("voice") or {}).get("vocab_allowed") or [])]
    pos = f.review_theme("pos")
    quote = str((pos or {}).get("common_quote") or "")
    term = next((v for v in vocab if v and v in quote.lower()), None)
    offer = f.best_active_offer()
    guess = ""
    if term:
        ts = f.trend_signal(term)
        guess = f"My guess is {term} — your reviews keep saying \"{quote}\""
        if ts and term in str(ts.get("query", "")).lower():
            guess += f", and '{ts.get('query')}' searches are up {pct(ts.get('delta_yoy'))} YoY"
        guess += "."
    elif offer and f.slug == "restaurants":
        ts = f.trend_for_merchant()
        guess = f"Is it still the {offer.split('@')[0].strip()}?"
        if ts:
            guess += f" '{ts.get('query')}' searches are up {pct(ts.get('delta_yoy'))} YoY."
    elif f.slug == "pharmacies":
        beat = f.current_beat()
        if beat and "—" in str(beat.get("note", "")):
            guess = f"My guess: {beat.get('note').split('—', 1)[1].strip().split(',')[0]} — that's the usual {beat.get('month_range')} {beat.get('note').split('—')[0].strip()}."
    else:
        opts = re.findall(r"— (.+)\?", q)
        opt_words = [w.strip() for w in re.split(r",| or ", opts[0])] if opts else []
        ts = None
        for w in opt_words:
            t = f.trend_signal(w.split()[0]) if w else None
            if t and w.split()[0].lower() in str(t.get("query", "")).lower():
                ts = t
                break
        ts = ts or f.trend_for_merchant()
        if ts:
            guess = f"My guess: '{ts.get('query')}' — those searches are up {pct(ts.get('delta_yoy'))} YoY."
    if guess:
        parts.append(guess)
    parts.append("Reply with the name and I'll turn it into a Google post plus a ready-made price reply for WhatsApp enquiries — 5 minutes of your time, max.")
    rat = ("curious_ask_due: asking-the-merchant lever (low-stakes, no commitment) with a data-backed guess from "
           f"{'their own review quote' if term else 'the category trend/seasonal data'}; reciprocity (Vera builds the post + reply); open-ended by design.")
    return Msg(" ".join(parts), "open_ended", rat, [first, q, "google post + price reply"])


def h_winback_merchant(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    since = p.get("days_since_expiry", f.sub.get("days_since_expiry"))
    dip = num(p.get("perf_dip_pct"))
    added = p.get("lapsed_customers_added_since_expiry")
    name = merchant_name(f.merchant)
    parts = []
    s = f"{sal}, it's been {fint(since)} days since {name}'s plan lapsed" if since else f"{sal}, {name}'s plan is currently inactive"
    parts.append(s + ".")
    bits = []
    if dip is not None and dip < 0:
        bits.append(f"calls are down {pct(dip)}")
    if added:
        bits.append(f"{fint(added)} more {f.people} have slipped into your lapsed list")
    if bits:
        parts.append("Since then " + " and ".join(bits) + ".")
    lapsed = num(f.agg.get("lapsed_90d_plus")) or num(f.agg.get("lapsed_180d_plus"))
    if lapsed:
        parts.append(f"That's {fint(lapsed)} lapsed {f.people} in total — the easiest revenue you have, because they already know you.")
    parts.append(_ask(f, "reactivate the plan and draft a win-back WhatsApp for those lapsed " + f.people))
    rat = ("winback_eligible: expired merchant; anchored on days since expiry + post-expiry dip + lapsed-customer count "
           "(loss aversion), single reactivation CTA bundled with a concrete win-back artifact.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, str(since), "reactivate"])


def h_ipl(f: Facts) -> Msg:
    first = salutation(f.merchant, f.slug)
    p = f.payload
    match, venue = p.get("match"), p.get("venue")
    t = nice_time(p.get("match_time_iso"))
    weeknight = p.get("is_weeknight")
    dig = f.digest_item("d_2026W17_ipl_window") or next(
        (d for d in f.category.get("digest") or [] if "ipl" in str(d.get("title", "")).lower()), None)
    parts = [f"Heads-up {first} — {match or 'IPL match'}" + (f" at {venue}" if venue else "") + (f" tonight, {t}" if t else " today") + "."]
    offer = f.best_active_offer()
    deliv = num(f.agg.get("delivery_orders_30d"))
    combo = next((o.get("title") for o in f.catalog() if "match" in str(o.get("title", "")).lower()), None)
    combo_s = combo.split("(")[0].strip() if combo else None
    if weeknight is False:
        if dig:
            parts.append("Weekend matches pull people into home watch-parties: magicpin order data shows covers down ~12% on match Saturdays (vs +18% on weeknights).")
        else:
            parts.append("Weekend matches pull people into home watch-parties, so dine-in promos underperform.")
        tonight = f"a delivery-only {combo_s}" if combo_s else "a delivery-only match combo"
        s = f"So skip the dine-in promo tonight and lean on delivery"
        s += f" — you already do {fint(deliv)} delivery orders a month" if deliv else ""
        parts.append(s + f": {tonight}, live before {t or 'the toss'}.")
        if offer and re.search(r"tue|wed|thu|weekday", offer, re.I):
            parts.append(f"Save your '{offer}' for the weeknight fixtures, where it'll actually pull covers.")
        action = "set up the combo with a Swiggy banner + an Insta story"
    else:
        if dig:
            parts.append("Weeknight matches have been the strong ones this season — covers up ~18% vs a normal weeknight (magicpin order data).")
        pitch = offer or combo_s or "a match-night combo"
        parts.append(f"Worth putting '{pitch}' up front for the pre-match crowd.")
        action = f"push '{pitch}' as a Google post + Insta story before the match"
    parts.append(_ask(f, action))
    rat = ("ipl_match_today: read is_weeknight from payload and applied the category digest (Saturday matches -12% covers, weeknights +18%) — "
           "contrarian judgment instead of a blanket match promo; respected the existing offer's day restriction; one CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [first, match or "IPL", action])


def h_review_theme(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    theme = p.get("theme")
    occ = p.get("occurrences_30d")
    quote = p.get("common_quote")
    trend = p.get("trend")
    if not theme:
        rt = f.review_theme("neg") or f.review_theme()
        if rt:
            theme, occ, quote = rt.get("theme"), rt.get("occurrences_30d"), rt.get("common_quote")
    name = merchant_name(f.merchant)
    parts = []
    if theme:
        s = f"{sal}, {fint(occ)} reviews of {name} in the last 30 days mention {humanize(theme).replace('delivery late', 'late delivery')}" if occ else \
            f"{sal}, a pattern is showing up in {name}'s recent reviews: {humanize(theme)}"
        if trend:
            s += f", and the trend is {trend}"
        parts.append(s + ".")
        if quote:
            parts.append(f"The one people will read first: \"{quote}\".")
        if f.sub.get("status") == "trial":
            parts.append(f"With your trial in its last {fint(f.sub.get('days_remaining'))} days, this is the one thing that can dent your rating.")
        pos = f.review_theme("pos")
        if pos and pos.get("theme") != theme and num(pos.get("occurrences_30d")):
            parts.append(f"Good news: {fint(pos.get('occurrences_30d'))} reviews praise your {humanize(pos.get('theme'))}, so a calm, specific reply goes a long way.")
        action = "draft polite public replies to these reviews + a one-line fix note for your team"
        rat_sig = f"theme={theme} ({occ} in 30d)"
    else:
        v = f.perf.get("views")
        parts.append(f"{sal}, a new theme is showing up in {name}'s latest Google reviews — worth seeing before more searchers do" + (f" ({fint(v)} people viewed your profile in the last 30 days)." if v else "."))
        action = "pull those reviews and draft replies for you"
        rat_sig = "theme not in payload — did not invent one; curiosity framing"
    parts.append(_ask(f, action))
    rat = f"review_theme_emerged: {rat_sig}; quoted the customer's own words where available; reputational loss-aversion + one CTA."
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, str(theme or "reviews"), action])


def h_milestone(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    metric = p.get("metric")
    now_v, ms = num(p.get("value_now")), num(p.get("milestone_value"))
    name = merchant_name(f.merchant)
    parts = []
    if metric and now_v is not None and ms is not None:
        label = f.metric_label(metric)
        if now_v < ms:
            parts.append(f"{sal}, {name} is {fint(ms - now_v)} {label} away from {fint(ms)} ({fint(now_v)} now).")
        else:
            parts.append(f"{sal}, {name} just crossed {fint(ms)} {label} — {fint(now_v)} and counting.")
        if "review" in metric and num(f.peer.get("avg_review_count")) and now_v >= num(f.peer.get("avg_review_count")):
            parts.append(f"That already puts you above the {fint(f.peer.get('avg_review_count'))}-review average for {f.slug} like yours.")
        pos = f.review_theme("pos")
        if pos and num(pos.get("occurrences_30d")):
            parts.append(f"{_cap(_theme_phrase(pos.get('theme')))} alone earned {fint(pos.get('occurrences_30d'))} positive mentions this month, so happy {f.people} are there to ask.")
        action = f"draft a one-tap review request you can send to today's {f.people}" if "review" in metric else "share this milestone as a Google post"
        sig = f"{metric} {fint(now_v)}->{fint(ms)}"
    else:
        ab = f.above_peer()
        if ab:
            m, mv, pv, _ = ab[0]
            ratio = mv / pv if pv else 1
            lead = "more than double" if ratio >= 2 else "well ahead of" if ratio >= 1.3 else "ahead of"
            if m == "ctr":
                parts.append(f"{sal}, milestone moment: {poss(name)} click-through rate hit {ctr_str(mv)} over the last 30 days — {lead} the {ctr_str(pv)} average for nearby {f.nouns}.")
            else:
                parts.append(f"{sal}, milestone moment: {name} logged {fint(mv)} {f.metric_label(m)} in the last 30 days — {lead} the {fint(pv)} average for nearby {f.nouns}.")
            sig = f"{m} {f.metric_value(m, mv)} vs peer {f.metric_value(m, pv)}"
        else:
            v = num(f.perf.get("views"))
            step = 1000 if (v or 0) >= 2000 else 500 if (v or 0) >= 500 else 100
            mark = int((v or 0) // step * step)
            parts.append(f"{sal}, {name} has crossed {fint(mark)} profile views in the last 30 days." if mark else f"{sal}, {name} hit a new milestone this month.")
            sig = f"views crossed {mark}"
        parts.append(f"A high point like this is the best moment to ask happy {f.people} for a Google review — warm asks convert far better than cold ones.")
        action = "draft a one-tap review request for your regulars"
    parts.append(_ask(f, action))
    rat = f"milestone_reached: {sig}; celebration + social-proof lever, converted into a single review-growth action."
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, sig, action])


def _thali_draft(f: Facts, offer: str) -> Optional[str]:
    price = num(re.sub(r"[^\d.]", "", offer.split("@")[-1])) if "@" in offer else None
    if not price:
        return None
    t1, t2, t3 = price - 10, price - 20, price - 30
    base = offer.split("@")[0].strip()
    return (f"Draft — {short_merchant(f.merchant)} Corporate {base.replace('Weekday Lunch ', '')} for offices in {f.locality}:\n"
            f"• 10-24 thalis/day @ {money(t1)} each (vs {money(price)} retail)\n"
            f"• 25-49 @ {money(t2)} each\n"
            f"• 50+ @ {money(t3)} each + free delivery\n"
            f"• Order by 5pm the day before; lunch delivered 12:30-1:30pm")


def h_planning(f: Facts) -> Msg:
    first = salutation(f.merchant, f.slug)
    p = f.payload
    topic = humanize(p.get("intent_topic") or "the plan")
    offer = f.best_active_offer()
    parts = [f"{first}, here's a first version of the {topic} you asked about — edit anything:"]
    draft = None
    note = ""
    tl = topic.lower()
    if ("thali" in tl or "corporate" in tl or "bulk" in tl) and offer:
        draft = _thali_draft(f, offer)
        hist = f.history_mentions("orders/day")
        if hist:
            m = re.search(r"(\d+)\s*orders/day", hist.get("body", ""))
            if m:
                note = f"Your weekday thali already averages {m.group(1)} orders/day, so bulk slots fit your kitchen rhythm."
    if draft is None and ("kids" in tl or "yoga" in tl or "camp" in tl or "program" in tl):
        hist = f.last_vera_msg()
        spec = hist.get("body", "") if hist else ""
        weeks = re.search(r"(\d+)-week", spec)
        per = re.search(r"(\d+)\s*classes/week", spec)
        age = re.search(r"age\s*(\d+\s*-\s*\d+)", spec)
        price = re.search(r"₹\s?[\d,]+", spec)
        bullets = [
            f"• {weeks.group(1) if weeks else '4'} weeks, {per.group(1) if per else '3'} classes/week, 45 min each",
            f"• Ages {age.group(1) if age else '7-12'}, small batches",
            "• Each class: warm-up games, animal poses, balance work, 5-min breathing wind-down",
        ]
        if price:
            bullets.append(f"• {price.group(0)} for the full camp")
        draft = f"Draft — {topic.title()}, {merchant_name(f.merchant)}:\n" + "\n".join(bullets)
        if hist and hist.get("ts"):
            note = f"(Same shape we discussed on {nice_date(hist.get('ts'))}.)"
    if draft is None:
        draft = f"Draft — {topic.title()}:\n• What: {topic}\n• Built around: {offer or 'your best-selling service'}\n• Launch: this week on Google + WhatsApp"
    parts.append("\n" + draft)
    if note:
        parts.append("\n" + note)
    if "thali" in tl or "corporate" in tl:
        action = f"draft a 3-line WhatsApp for office admins around {f.locality} to go with it"
    else:
        action = "publish it as a Google post + an Insta carousel"
    parts.append("\n" + _ask(f, action))
    body = " ".join(parts).replace(" \n", "\n")
    rat = ("active_planning_intent: merchant already said yes to exploring this, so skipped qualifying and delivered a concrete "
           "draft built from their live offer/price and the earlier Vera spec; next step is one binary CTA (intent handoff done right).")
    return Msg(body, "binary_yes_no", rat, [first, topic, action])


def h_supply(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    mol = p.get("molecule") or "the affected product"
    batches = p.get("affected_batches") or []
    mfr = p.get("manufacturer")
    it = f.digest_item(p.get("alert_id")) or next((d for d in f.category.get("digest") or [] if d.get("kind") == "alert"), None)
    parts = [f"{sal}, urgent: voluntary recall on {len(batches) if batches else 'some'} {mol} batch{'es' if len(batches) != 1 else ''}"
             + (f" — {', '.join(batches)}" if batches else "") + (f" ({mfr})" if mfr else "") + "."]
    if it:
        sents = [re.sub(r"\s*\([^)]*\)", "", x).strip() for x in re.split(r"(?<=\.)\s", str(it.get("summary", ""))) if x.strip()]
        why = next((x for x in sents if "flag" in x.lower() or "potency" in x.lower()), sents[0] if sents else "")
        src = _source(it)
        if why:
            parts.append(why.rstrip(".") + (f" ({src})." if src else "."))
        safety = [x for x in sents if "risk" in x.lower()]
        if safety:
            parts.append(safety[0].rstrip(".") + ".")
    chronic = num(f.agg.get("chronic_rx_count"))
    hist = (f.merchant.get("conversation_history") or [])[-2:]
    if mol and any(mol.lower() in str(h.get("body", "")).lower() for h in hist) and any(str(h.get("from")) == "merchant" for h in hist):
        parts.append("These are the confirmed batch numbers for the list you asked for.")
    if chronic:
        parts.append(f"You have {fint(chronic)} chronic-Rx customers on file — I can filter everyone dispensed {mol} from these batches.")
    parts.append(_ask(f, "pull that list and draft the customer WhatsApp + replacement-pickup note"))
    rat = ("supply_alert (urgency 5): batch numbers + manufacturer from payload, safety framing from the category alert; "
           "sized the impact from merchant's chronic-Rx count (no invented affected number); continuity with the merchant's earlier ask; end-to-end workflow CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, f"{mol} recall", ", ".join(batches)])


def h_category_seasonal(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    trends = p.get("trends") or []
    season = humanize(p.get("season") or "")
    parsed = []
    for t in trends:
        m = re.match(r"(.+?)_demand_([+-]?\d+)", str(t))
        if m:
            parsed.append((humanize(m.group(1)).replace("cold cough", "cold & cough"), int(m.group(2))))
    ups = [x for x in parsed if x[1] > 0]
    downs = [x for x in parsed if x[1] < 0]
    parts = []
    if parsed:
        up_txt = ", ".join(f"{n} +{v}%" for n, v in sorted(ups, key=lambda x: -x[1]))
        s = f"{sal}, the {season.split(' ')[0] if season else 'seasonal'} demand shift is here: {up_txt}"
        if downs:
            s += "; " + ", ".join(f"{n} {v}%" for n, v in downs)
        parts.append(s + ".")
    else:
        beat = f.current_beat()
        parts.append(f"{sal}, seasonal shift for {f.slug}: " + (f"{beat.get('month_range')} — {beat.get('note')}." if beat else "demand is moving this month."))
    dig = next((d for d in f.category.get("digest") or [] if d.get("kind") == "seasonal"), None)
    if dig and dig.get("actionable"):
        parts.append(f"Worth a 20-minute shelf reset: {dig.get('actionable')[0].lower() + _strip_period(dig.get('actionable'))[1:]}.")
    offer = next((o for o in f.active_offers() if "delivery" in o.lower()), None) or f.best_active_offer()
    if offer and "₹" in offer:
        parts.append(f"Bundle the essentials into a summer kit that clears your '{offer}' threshold.")
    total = num(f.agg.get("total_unique_ytd"))
    action = f"draft a 'summer essentials' WhatsApp for your {fint(total)} customers" if total else "draft a 'summer essentials' post + WhatsApp"
    parts.append(_ask(f, action))
    rat = "category_seasonal: quantified demand shifts from payload + digest shelf guidance; tied to merchant's delivery offer and customer base; one CTA."
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, season or "seasonal shift", action])


def h_gbp_unverified(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    up = num(p.get("estimated_uplift_pct"))
    path = str(p.get("verification_path") or "")
    name = merchant_name(f.merchant)
    parts = [f"{sal}, {poss(name)} Google profile is still unverified."]
    g = f.peer_gap("views")
    if g:
        parts.append(f"You're at {fint(g[0])} profile views in 30 days vs the {fint(g[1])} average for nearby {f.nouns}" + (f" — verified listings see roughly {pct(up)} more visibility." if up else "."))
    elif up:
        parts.append(f"Verified listings see roughly {pct(up)} more visibility.")
    if path:
        steps = [("a " + humanize(x).strip()) for x in path.split("_or_")]
        parts.append(f"It's a one-time step — {' or '.join(steps)}.")
    parts.append(_ask(f, "start the verification for you today"))
    rat = "gbp_unverified: single fix with a quantified upside (payload uplift) and the merchant's own views vs peer; effort-externalising CTA."
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, "verification", "start"])


def h_dormant(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    days = p.get("days_since_last_merchant_message")
    name = merchant_name(f.merchant)
    parts = []
    lead = f"Hi {sal}" if not sal.startswith("Dr.") else sal
    wd = f.worst_delta()
    if wd and wd[1] > -0.05:
        wd = None
    bp = f.below_peer()
    ab = f.above_peer()
    if wd:
        m, d = wd
        parts.append(f"{lead} — no pitch, just one number worth your attention: {poss(name)} {f.metric_label(m)} are down {pct(d)} this week.")
        sig = f"{m} {pct(d, True)}"
    elif bp:
        m, mv, pv, _ = bp[0]
        parts.append(f"{lead} — no pitch, just one number: {name} is at {f.metric_value(m, mv)} {f.metric_label(m)} in 30 days vs the {f.metric_value(m, pv)} average for nearby {f.nouns}.")
        sig = f"{m} below peer"
    elif ab:
        m, mv, pv, _ = ab[0]
        parts.append(f"{lead} — one good number: {name} pulled {f.metric_value(m, mv)} {f.metric_label(m)} in 30 days, ahead of the {f.metric_value(m, pv)} local average.")
        sig = f"{m} above peer"
    else:
        parts.append(f"{lead} — it's been a while, so here's one quick read on {name}.")
        sig = "re-engagement"
    beat = f.current_beat()
    if beat:
        parts.append(f"And {beat.get('month_range')} is \"{_strip_period(beat.get('note'))}\" for {f.slug} — good timing to be visible.")
    parts.append(_ask(f, "send a 2-minute breakdown of what's holding the listing back"))
    rat = (f"dormant_with_vera{f' ({days}d silent)' if days else ''}: re-engage with one verifiable number ({sig}) + seasonal timing, "
           "no hard sell; curiosity + reciprocity; one low-effort CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, sig, "breakdown"])


def h_weather(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    temp = p.get("temperature_c") or p.get("temp_c") or p.get("temperature")
    city = p.get("city") or f.city
    cond = humanize(p.get("condition") or f.trigger.get("kind", "").replace("weather_", ""))
    parts = [f"{sal}, {cond} in {city} today" + (f" — {temp}°C" if temp else "") + "."]
    tips = {
        "restaurants": ("delivery demand jumps when people avoid stepping out", "push a cool-drinks + delivery combo on your listing today"),
        "pharmacies": ("ORS, electrolytes and sunscreen move fastest on days like this", "draft a 'beat the heat' WhatsApp for your customers"),
        "gyms": ("members skip outdoor cardio — indoor and early-morning slots fill up", "post your early-morning class timings for today"),
        "salons": ("walk-ins drop midday; evening slots matter more", "post an evening-slot offer for today"),
        "dentists": ("patients reschedule midday appointments on extreme-weather days", "send a confirm-or-reschedule note to today's patients"),
    }.get(f.slug, ("customer behaviour shifts on days like this", "draft a same-day post for your listing"))
    parts.append(_cap(tips[0]) + ".")
    parts.append(_ask(f, tips[1]))
    rat = f"{f.trigger.get('kind')}: weather facts from payload, category-specific behavioural read, one same-day action."
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, cond, tips[1]])


def h_trend_movement(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    q = p.get("query")
    d = num(p.get("delta_yoy") or p.get("delta_pct"))
    ts = None
    if not q:
        ts = f.trend_for_merchant()
        if ts:
            q, d = ts.get("query"), num(ts.get("delta_yoy"))
    if not q:
        return h_generic(f)
    parts = [f"{sal}, searches for '{q}' are up {pct(d)}" + (" year-on-year" if (ts or p.get('delta_yoy')) else "") + "."]
    seg = (ts or p).get("segment_age")
    if seg:
        parts.append(f"Most of it is the {humanize(seg)} age band.")
    offer = f.best_active_offer()
    if offer:
        parts.append(f"Your '{offer}' is the closest match to that demand — right now nothing on your listing says so.")
        action = f"write a Google post connecting '{offer}' to this search"
    else:
        sugg = f.suggested_catalog_offer()
        parts.append("There's no live offer on your listing that answers this search yet.")
        action = f"set up '{sugg}' and a post targeting this search" if sugg else "draft a post targeting this search"
    parts.append(_ask(f, action))
    rat = "category_trend_movement: quantified search trend + audience, mapped to merchant's own offer; one CTA."
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, q, action])


def _payload_facts(f: Facts, limit: int = 3) -> list[str]:
    out = []
    for k, v in (f.payload or {}).items():
        if k in ("placeholder", "metric_or_topic", "category", "merchant_id", "customer_id") or isinstance(v, (dict, list)):
            continue
        if v in (None, "", True, False):
            continue
        key = humanize(k).replace(" pct", "").replace(" iso", "").replace(" c", " (°C)")
        val = pct(v) if k.endswith("_pct") and num(v) is not None else (nice_date(v) if isinstance(v, str) and parse_date(v) else
                                                                         fint(v) if num(v) is not None else str(v))
        out.append(f"{key}: {val}")
        if len(out) >= limit:
            break
    return out


def h_local_news(f: Facts) -> Msg:
    sal = salutation(f.merchant, f.slug)
    p = f.payload
    head = p.get("headline") or p.get("title") or p.get("event") or humanize(p.get("topic") or "a local event")
    where = p.get("city") or p.get("locality") or f.locality
    parts = [f"{sal}, heads-up: {_strip_period(str(head))}" + (f" ({where})" if where else "") + "."]
    if p.get("impact") or (p.get("duration_hours") and str(p.get("duration_hours")) not in str(head)):
        parts.append(_cap(humanize(p.get("impact") or f"expected to last {p.get('duration_hours')} hours")) + ".")
    tip = {
        "restaurants": ("fewer people will travel in, so delivery and takeaway will carry the day", "push a delivery-first post for today"),
        "pharmacies": ("walk-ins drop, but WhatsApp orders rise", "post that you're open and delivering today"),
        "salons": ("some clients will cancel — a good day to fill gaps with walk-ins from nearby", "send a same-day slot message to nearby regulars"),
        "gyms": ("members may skip — a short home-workout post keeps them engaged", "post a 20-minute home workout for your members"),
        "dentists": ("some patients may miss appointments", "send a confirm-or-reschedule note to today's patients"),
    }.get(f.slug, ("customer plans may shift today", "post a same-day update on your listing"))
    parts.append(_cap(tip[0]) + ".")
    parts.append(_ask(f, tip[1]))
    rat = "local_news_event: event facts from payload only, category-specific impact read, one same-day action."
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, str(head), tip[1]])


def h_generic(f: Facts) -> Msg:
    """Fallback for unknown merchant-facing kinds: payload facts (prettified) + one merchant signal + one fix."""
    sal = salutation(f.merchant, f.slug)
    kind = humanize(f.trigger.get("kind") or "update")
    facts = _payload_facts(f, 2)
    parts = [f"{sal}, flagging one thing for {merchant_name(f.merchant)} — {kind}" + (f" ({'; '.join(facts)})" if facts else "") + "."]
    bp = f.below_peer()
    if bp:
        m, mv, pv, _ = bp[0]
        parts.append(f"Worth pairing with one number: your {f.metric_label(m)} are {f.metric_value(m, mv)} vs the {f.metric_value(m, pv)} peer average.")
    cause, fix = _diagnose(f)
    parts.append(_ask(f, fix))
    rat = f"{kind}: unknown/sparse trigger kind — used only payload fields present plus one merchant-vs-peer fact; one CTA. (LLM, if enabled, rewrites this kind.)"
    return Msg(" ".join(parts), "binary_yes_no", rat, [sal, kind, fix])


# --------------------------------------------------------------------------- customer-facing handlers


def _sender(f: Facts) -> str:
    name = merchant_name(f.merchant)
    first = owner_first(f.merchant)
    if f.slug == "dentists":
        if first and first.lower() in name.lower():
            return name
        return f"Dr. {first}'s clinic, {name}" if first else name
    if f.slug == "pharmacies":
        return f"{name}, {f.locality}" if f.locality else name
    return f"{first} from {name}" if first else name


def _greet(f: Facts, cname: str, parent: Optional[str]) -> str:
    lang = customer_lang(f.customer)
    who = parent or cname
    if lang == "hi":
        return f"Namaste {who} ji" if who else "Namaste"
    if lang in REGIONAL_GREETING:
        return f"{REGIONAL_GREETING[lang]} {who}" if who else REGIONAL_GREETING[lang]
    return f"Hi {who}" if who else "Hi"


def _emoji(f: Facts) -> str:
    return {"dentists": " 🦷", "salons": " ✨", "gyms": " 💪", "restaurants": " 🍽️", "pharmacies": ""}.get(f.slug, "")


def _cust_offer(f: Facts, purpose: str) -> Optional[str]:
    """Only the merchant's own active offers are ever quoted to customers."""
    if purpose == "winback":
        return f.free_active_offer() or f.best_active_offer()
    return f.price_active_offer() or f.best_active_offer()


def _visit_ago(f: Facts) -> Optional[str]:
    lv = f.customer_rel().get("last_visit")
    if not lv:
        return None
    return nice_date(lv, with_year=True)


def _header(f: Facts, greet: str, lang: str) -> str:
    """'Hi Priya, Dr. Meera's Dental Clinic here 🦷' — always ends cleanly before the next sentence."""
    sender = _sender(f)
    emo = _emoji(f)
    if lang in ("hi", "hinglish"):
        return f"{greet}, {sender} se{emo}" + ("" if emo else ".")
    return f"{greet}, {sender} here{emo}" + ("" if emo else ".")


def _is_lapsed(f: Facts) -> bool:
    return str((f.customer or {}).get("state", "")).lower() in ("lapsed_soft", "lapsed_hard", "churned")


SVC_DUE = {"dentists": "routine check-up", "salons": "next salon visit", "gyms": "fitness check-in",
           "pharmacies": "monthly restock", "restaurants": "next visit"}


def c_recall(f: Facts) -> Msg:
    cname, parent = customer_first(f.customer)
    lang = customer_lang(f.customer)
    p = f.payload
    slots = [s.get("label") for s in p.get("available_slots") or [] if isinstance(s, dict) and s.get("label")]
    service = humanize(p.get("service_due") or "").replace("6 month", "6-month").replace("touchup", "touch-up")
    last = p.get("last_service_date") or f.customer_rel().get("last_visit")
    offer = _cust_offer(f, "winback" if _is_lapsed(f) else "recall")
    svc_words = [w for w in re.split(r"\W+", service.lower()) if len(w) >= 3 and w not in ("month", "touch", "session", "due")]
    scored = sorted(((sum(1 for w in svc_words if re.search(r"\b" + w + r"\b", o.lower())), o) for o in f.active_offers()),
                    key=lambda x: -x[0])
    matched = scored[0][1] if scored and scored[0][0] > 0 else None
    offer = matched or offer
    svc_txt = service or SVC_DUE.get(f.slug, "next visit")
    pref = f.slot_pref_text()
    parts = [_header(f, _greet(f, cname, parent), lang)]
    if lang in ("hinglish", "hi") and f.slug == "restaurants" and not slots:
        fav = f.customer_rel().get("favourite_dish")
        parts.append((f"Aapka last order {nice_date(last, with_year=True)} ko tha — " if last else "")
                     + (f"aapka favourite {fav} bas ek message door hai." if fav else "aapka usual order bas ek message door hai."))
        if offer:
            parts.append(f"{offer} abhi chal raha hai.")
        cta = "Reply YES — hum aapka order ready rakhenge."
    elif lang == "hinglish":
        parts.append(f"Aapki last visit {nice_date(last, with_year=True)} ko thi — ab aapka {svc_txt} due hai." if last else f"Aapka {svc_txt} due hai.")
        if slots:
            parts.append(f"Aapke liye {len(slots)} slots rakhe hain: " + " ya ".join(slots) + ".")
        if offer:
            parts.append(f"{offer} abhi chal raha hai.")
        cta = ("Reply " + ", ".join(f"{i + 1} for {s.split(',')[0]}" for i, s in enumerate(slots)) + " — ya apna time batayein.") if slots \
            else (f"Reply YES — hum aapke {pref} time par slot confirm kar denge." if pref else "Reply YES — hum aapke time par slot confirm kar denge.")
    elif lang == "hi":
        parts.append(f"Aapki pichhli visit {nice_date(last, with_year=True)} ko thi — ab {svc_txt} ka samay ho gaya hai." if last else f"Aapka {svc_txt} ka samay ho gaya hai.")
        if slots:
            parts.append("Uplabdh slot: " + " ya ".join(slots) + ".")
        if offer:
            parts.append(f"{offer} abhi chal raha hai.")
        cta = ("Reply " + ", ".join(f"{i + 1} ({s.split(',')[0]})" for i, s in enumerate(slots)) + " — ya apna samay batayein.") if slots else "Booking ke liye HAAN reply karein."
    elif f.slug == "restaurants" and not slots:
        fav = f.customer_rel().get("favourite_dish")
        parts.append(f"Your last order with us was on {nice_date(last, with_year=True)} — " if last else "")
        parts[-1] += (f"your {fav} is just a message away." if fav else "your usual is just a message away.")
        if offer:
            parts.append(f"{offer} is on right now.")
    else:
        parts.append(f"Your last visit was on {nice_date(last, with_year=True)}, so your {svc_txt} is now due." if last else f"Your {svc_txt} is now due.")
        if slots:
            parts.append(f"We've kept {len(slots)} slot{'s' if len(slots) > 1 else ''} for you: " + " or ".join(slots) + ".")
        if offer:
            parts.append(f"Open for you right now: {offer}" + (" — a good way to see where you're at." if "analysis" in offer.lower() else "."))
        if slots:
            cta = "Reply " + ", ".join(f"{i + 1} for {s.split(',')[0]}" for i, s in enumerate(slots)) + ", or tell us a time that works."
        elif f.slug == "pharmacies":
            cta = "Reply YES and we'll keep your regular order packed and ready."
        elif f.slug == "restaurants":
            cta = "Reply YES and we'll have it ready when you are."
        else:
            cta = f"Reply YES and we'll book a {pref} slot for you." if pref else "Reply YES and we'll book a slot that suits you."
    parts.append(cta)
    rat = (f"{f.trigger.get('kind')} (customer-scope, merchant_on_behalf): last service {last}; "
           f"{'real open slots from payload' if slots else 'no slots in payload, so none invented'}"
           f"{', preference: ' + pref if pref else ''}; merchant's own offer only; language={lang}; consent checked.")
    return Msg(" ".join(parts), "multi_choice_slot" if len(slots) > 1 else "binary_yes_no", rat, [cname, _sender(f), svc_txt] + slots)


def c_bridal(f: Facts) -> Msg:
    cname, parent = customer_first(f.customer)
    p = f.payload
    wd = p.get("wedding_date") or f.customer_prefs().get("wedding_date")
    trial = p.get("trial_completed")
    days = p.get("days_to_wedding")
    step = humanize(p.get("next_step_window_open") or "pre-wedding prep")
    step = step.replace("skin prep program 30day", "30-day skin-prep program")
    parts = [f"{_greet(f, cname, parent)} 💍 {_sender(f)} here."]
    if trial:
        parts.append(f"Hope you loved your bridal trial on {nice_date(trial)}!")
    if wd:
        parts.append(f"With the wedding on {nice_date(wd, with_year=True)}" + (f" ({fint(days)} days to go)" if days else "")
                     + f", this is the right window to start the {step}, so your skin settles well before the big day.")
    else:
        parts.append(f"This is the right window to start the {step}.")
    pref = f.slot_pref_text()
    slot_txt = f"a {pref} slot" if pref else "a slot"
    parts.append(f"Shall I hold {slot_txt} for your first session next week? Reply YES and we'll share the timings.")
    rat = ("bridal/wedding follow-up (customer-scope): wedding date + trial date + open window from payload; honoured slot preference; "
           "no programme price quoted because none exists in the merchant's offers; single YES CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [cname, str(wd), step])


REASON_BACK = {
    "dentists": ("A quick check-up now keeps small issues from turning into bigger ones.",
                 "Ek chhota check-up abhi karwa lein toh chhoti problem badi nahi banti."),
    "salons": ("Your next appointment is the perfect excuse for a little me-time.",
               "Thoda me-time toh banta hai!"),
    "gyms": ("A couple of easy sessions is all it takes to get the rhythm back.",
             "Bas 2-3 easy sessions, aur rhythm wapas aa jayega."),
    "pharmacies": ("We can keep your regular medicines packed and ready — or deliver them.",
                   "Aapki regular dawaiyan hum pack karke ready rakh sakte hain, ya ghar bhej sakte hain."),
    "restaurants": ("Your usual is just a message away.", "Aapka usual order bas ek message door hai."),
}


def c_winback(f: Facts) -> Msg:
    cname, parent = customer_first(f.customer)
    lang = customer_lang(f.customer)
    p = f.payload
    days = num(p.get("days_since_last_visit"))
    focus = humanize(p.get("previous_focus") or f.customer_prefs().get("training_focus") or "").replace("_", " ")
    months = p.get("previous_membership_months")
    offer = _cust_offer(f, "winback")
    last = _visit_ago(f)
    fav = f.customer_rel().get("favourite_dish")
    reason = REASON_BACK.get(f.slug, ("We'd love to see you again.", "Aapko dobara dekh kar khushi hogi."))
    if f.slug == "restaurants" and fav:
        reason = (f"Your {fav} is just a message away.", f"Aapka favourite {fav} bas ek message door hai.")
    weeks = int(round(days / 7)) if days else None
    parts = [_header(f, _greet(f, cname, parent), lang)]
    pref = f.slot_pref_text()
    if lang in ("hinglish", "hi"):
        if weeks:
            parts.append(f"Aapko dekhe lagbhag {weeks} hafte ho gaye — koi baat nahi, sabke saath hota hai.")
        elif last:
            parts.append(f"Aapki last visit {last} ko thi — kaafi time ho gaya! Koi baat nahi, sabke saath hota hai.")
        parts.append(reason[1])
        if offer:
            parts.append(f"Wapas aane par {offer} aapke liye ready hai.")
        if f.slug == "pharmacies":
            parts.append("Aapka regular order ready rakh dein? Reply HAAN.")
        elif f.slug == "restaurants":
            parts.append("Is weekend ke liye table rakh dein? Reply HAAN.")
        else:
            parts.append("Is hafte ek slot hold kar doon? Reply HAAN — koi commitment nahi.")
    else:
        if weeks:
            parts.append(f"It's been about {weeks} weeks since your last visit — it happens to everyone, no judgment.")
        elif last:
            parts.append(f"We haven't seen you since {last} — it happens to everyone, no judgment.")
        if focus and f.slug == "gyms":
            parts.append(f"Since {focus} was your focus" + (f" over your {months} months with us" if months else "")
                         + ", a couple of easy sessions is all it takes to restart.")
        else:
            parts.append(reason[0])
        if offer:
            parts.append(f"Open for you right now: {offer}" + (" — a good way to see where you're at." if "analysis" in offer.lower() else "."))
        if f.slug == "pharmacies":
            parts.append("Reply YES and we'll keep your regular order ready.")
        elif f.slug == "restaurants":
            parts.append("Want us to keep a table for you this weekend? Reply YES.")
        else:
            slot = f" {pref.replace('weekday evening', 'weekday-evening')}" if pref else ""
            parts.append(f"Want me to hold a{slot} spot for you this week? Reply YES — no commitment.")
    rat = (f"{f.trigger.get('kind')} (customer-scope): {'%d days since last visit' % days if days else 'last visit ' + str(last)}, "
           f"prior focus '{focus or 'n/a'}'; no-shame tone, merchant's own offer only (none invented), commitment-free single CTA; language={lang}; consent checked.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [cname, str(weeks or last), offer or ""])


def c_trial_followup(f: Facts) -> Msg:
    cname, parent = customer_first(f.customer)
    lang = customer_lang(f.customer)
    p = f.payload
    trial = p.get("trial_date") or f.customer_rel().get("last_visit")
    opts = [o.get("label") for o in p.get("next_session_options") or [] if isinstance(o, dict) and o.get("label")]
    svcs = [humanize(x) for x in f.customer_rel().get("services_received") or [] if "trial" in str(x)]
    what = svcs[-1].replace(" trial", " trial class") if svcs else {"gyms": "trial session", "salons": "first visit",
                                                                     "pharmacies": "first order", "dentists": "first visit",
                                                                     "restaurants": "first visit"}.get(f.slug, "first visit")
    greet = _greet(f, cname, parent)
    parts = [_header(f, greet, lang).replace(" here 💪", " here 🙏") if "yoga" in merchant_name(f.merchant).lower() else _header(f, greet, lang)]
    had_trial = bool(p.get("trial_date") or svcs)
    if trial and had_trial:
        if parent:
            parts.append(f"{cname} came in for a {what} on {nice_date(trial)} — we hope it was fun!")
        else:
            parts.append(f"Thanks for coming in for your {what} on {nice_date(trial)}!")
    elif trial:
        parts.append(f"Thanks for coming in on {nice_date(trial)} — hope you enjoyed it!")
    pref = f.slot_pref_text()
    if opts:
        match_pref = pref and pref.split()[0][:3].lower() in opts[0].lower()
        parts.append(f"The next session is {opts[0]}" + (f" — {pref}, as you prefer" if match_pref else "") + ".")
        parts.append(f"Shall I save {cname + chr(39) + 's' if parent else 'your'} spot? Reply YES.")
    else:
        offer = _cust_offer(f, "recall")
        if offer:
            parts.append(f"{offer} is open if you'd like to continue.")
        if f.slug == "pharmacies":
            parts.append("Reply YES and we'll keep your next order ready.")
        else:
            parts.append(f"Want me to book {'the' if parent else 'your'} next session" + (f" for a {pref} slot" if pref else " this week") + "? Reply YES.")
    rat = ("trial_followup (customer-scope): trial date + next session from payload (none invented when absent), "
           "preference honoured, parent addressed when the member is a child; single CTA.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [cname, str(trial), opts[0] if opts else ""])


def c_refill(f: Facts) -> Msg:
    if f.slug != "pharmacies":
        return c_recall(f)
    cname, parent = customer_first(f.customer)
    lang = customer_lang(f.customer)
    p = f.payload
    mols = p.get("molecule_list") or []
    runs_out = p.get("stock_runs_out_iso")
    saved = p.get("delivery_address_saved") or str(f.customer_prefs().get("delivery_address", "")) == "saved"
    senior = bool((f.customer.get("identity") or {}).get("senior_citizen"))
    senior_offer = next((o for o in f.active_offers() if "senior" in o.lower()), None)
    deliv_offer = next((o for o in f.active_offers() if "delivery" in o.lower()), None)
    thr = re.search(r"₹\s?[\d,]+", deliv_offer or "")
    via = str(f.customer_prefs().get("channel", ""))
    sender = _sender(f)
    recall_clash = [m for m in mols if any(m.lower() in str(d.get("title", "")).lower() and d.get("kind") == "alert"
                                            for d in f.category.get("digest") or [])]
    if cname.lower().startswith(("mr", "mrs", "ms")):
        honor = cname.split()[-1] + " ji"
    else:
        honor = f"{cname} ji" if cname else "aap"
    senior_pct = re.search(r"\d+%", senior_offer or "")
    if lang in ("hi", "hinglish"):
        parts = [f"Namaste — {sender} se."]
        subj = f"{honor} ki" if "via" in via else "Aapki"
        if mols:
            parts.append(f"{subj} {len(mols)} monthly dawaiyan ({', '.join(mols)}) "
                         + (f"{nice_date(runs_out)} tak khatam ho jayengi." if runs_out else "refill ke liye due hain."))
        else:
            parts.append(f"{subj} regular dawaiyon ka refill due hai.")
        parts.append("Same dose, same brand ka pack ready kar dete hain.")
        if recall_clash:
            parts.append(f"{recall_clash[0].capitalize()} ka batch hum is mahine ki recall list se match karke hi denge.")
        perks = []
        if senior and senior_offer:
            perks.append(f"senior citizen {senior_pct.group(0) if senior_pct else ''} chhoot lagegi".replace("  ", " "))
        if deliv_offer and saved:
            perks.append(f"{thr.group(0) + ' se upar ' if thr else ''}free home delivery saved address par")
        if perks:
            parts.append(_cap(" aur ".join(perks)) + ".")
        parts.append("Dispatch ke liye HAAN reply karein, ya dose mein koi badlaav ho toh bata dijiye.")
    else:
        parts = [f"Hello — {sender} here."]
        subj = f"{honor}'s" if "via" in via else "Your"
        parts.append(f"{subj} monthly medicines ({', '.join(mols)}) run out on {nice_date(runs_out)}." if mols and runs_out else f"{subj} regular medicines are due for a refill.")
        parts.append("We can keep the same dose and brand packed and ready.")
        if recall_clash:
            parts.append(f"We'll check the {recall_clash[0]} batch against this month's recall list before packing.")
        perks = []
        if senior and senior_offer:
            perks.append(senior_offer.replace("OFF", "off") + " applies")
        if deliv_offer and saved:
            perks.append(f"free home delivery to your saved address{' on orders above ' + thr.group(0) if thr else ''}")
        if perks:
            parts.append(_cap("; ".join(perks)) + ".")
        parts.append("Reply YES to dispatch, or tell us if the dose has changed.")
    rat = ("chronic_refill_due (customer-scope): molecules + run-out date from payload; merchant's own senior/delivery offers only (no invented totals); "
           f"addressed {'via family member' if 'via' in via else 'to the customer'}; {'cross-checked against the active category recall; ' if recall_clash else ''}language={lang}.")
    return Msg(" ".join(parts), "binary_yes_no", rat, [cname, ", ".join(mols), str(runs_out)])


APPT_NOUN = {"restaurants": ("table booking", "table booking"), "gyms": ("session", "session"),
             "pharmacies": ("pickup", "pickup"), "dentists": ("appointment", "appointment"),
             "salons": ("appointment", "appointment")}


def c_appointment(f: Facts) -> Msg:
    cname, parent = customer_first(f.customer)
    lang = customer_lang(f.customer)
    p = f.payload
    when = p.get("slot_label") or p.get("label") or ""
    t = nice_time(p.get("appointment_iso") or p.get("iso") or p.get("time_iso") or p.get("slot_iso"))
    svc = humanize(p.get("service") or "")
    noun = APPT_NOUN.get(f.slug, ("appointment", "appointment"))[0]
    tm = when or (f"tomorrow at {t}" if t else "tomorrow")
    loc = f.locality
    parts = [_header(f, _greet(f, cname, parent), lang)]
    if lang in ("hi", "hinglish"):
        parts.append(f"Reminder: aapka {svc + ' ' if svc else ''}{noun} {tm.replace('tomorrow', 'kal')} hai" + (f", hamare {loc} branch par." if loc else "."))
        parts.append("Confirm karne ke liye YES reply karein — time badalna ho toh bata dijiye, hum shift kar denge.")
    else:
        parts.append(f"Quick reminder: your {svc + ' ' if svc else ''}{noun} with us is {tm}" + (f" at our {loc} {f.noun}." if loc else "."))
        parts.append("Reply YES to confirm — or tell us a better time and we'll move it.")
    rat = "appointment_tomorrow (customer-scope): reminder with a single confirm-or-reschedule CTA; no time invented when payload lacks one; language honoured."
    return Msg(" ".join(parts), "binary_yes_no", rat, [cname, _sender(f), tm])


def c_generic(f: Facts) -> Msg:
    kind = str(f.trigger.get("kind", ""))
    if "slot" in kind or "recall" in kind:
        return c_recall(f)
    if "lapse" in kind or "winback" in kind:
        return c_winback(f)
    if "refill" in kind:
        return c_refill(f)
    if "appointment" in kind:
        return c_appointment(f)
    if "trial" in kind:
        return c_trial_followup(f)
    if "wedding" in kind or "bridal" in kind:
        return c_bridal(f)
    return c_recall(f)


# --------------------------------------------------------------------------- dispatch

MERCHANT_HANDLERS: dict[str, Callable[[Facts], Msg]] = {
    "research_digest": h_research, "category_research_digest_release": h_research, "research_digest_release": h_research,
    "regulation_change": h_regulation, "compliance_alert": h_regulation,
    "cde_opportunity": h_cde,
    "competitor_opened": h_competitor,
    "perf_dip": h_perf_dip,
    "seasonal_perf_dip": h_seasonal_dip,
    "perf_spike": h_perf_spike,
    "renewal_due": h_renewal,
    "festival_upcoming": h_festival, "festival": h_festival,
    "curious_ask_due": h_curious, "scheduled_recurring": h_curious,
    "winback_eligible": h_winback_merchant,
    "ipl_match_today": h_ipl,
    "review_theme_emerged": h_review_theme,
    "milestone_reached": h_milestone,
    "active_planning_intent": h_planning,
    "supply_alert": h_supply,
    "category_seasonal": h_category_seasonal,
    "gbp_unverified": h_gbp_unverified,
    "dormant_with_vera": h_dormant,
    "weather_heatwave": h_weather, "weather_alert": h_weather, "weather_rain": h_weather,
    "category_trend_movement": h_trend_movement, "trend_movement": h_trend_movement,
    "local_news_event": h_local_news, "local_event": h_local_news, "news_event": h_local_news,
}

CUSTOMER_HANDLERS: dict[str, Callable[[Facts], Msg]] = {
    "recall_due": c_recall, "unplanned_slot_open": c_recall,
    "wedding_package_followup": c_bridal, "bridal_followup": c_bridal,
    "customer_lapsed_hard": c_winback, "customer_lapsed_soft": c_winback, "winback": c_winback,
    "trial_followup": c_trial_followup,
    "chronic_refill_due": c_refill,
    "appointment_tomorrow": c_appointment,
}

KNOWN_KINDS = set(MERCHANT_HANDLERS) | set(CUSTOMER_HANDLERS)

TAB00_FIX = re.compile(r"https?://\S+|www\.\S+")


def _scrub(body: str, taboos: list[str]) -> str:
    body = TAB00_FIX.sub("", body)
    body = re.sub(r"\bGBP\b", "Google profile", body)  # internal jargon -> plain words
    for t in taboos:
        base = re.sub(r"\s*\(.*?\)", "", t).strip()
        if base and len(base) > 3:
            body = re.sub(re.escape(base), "", body, flags=re.I)
    return clean_spaces(body.replace(" ,", ",")) if "\n" not in body else "\n".join(clean_spaces(x) for x in body.split("\n"))


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None,
            now: Optional[str] = None) -> dict:
    """Main entry. Returns a dict; if 'skip' is True the bot should not send."""
    f = Facts(category, merchant, trigger, customer, now)
    kind = str(trigger.get("kind") or "")
    scope = str(trigger.get("scope") or ("customer" if trigger.get("customer_id") else "merchant"))
    is_customer = scope == "customer" or bool(trigger.get("customer_id"))
    sk = trigger.get("suppression_key") or f"{kind}:{merchant.get('merchant_id')}:{trigger.get('customer_id') or ''}"
    if is_customer:
        ok, why = f.consent_ok()
        if not ok:
            return {"skip": True, "skip_reason": why, "suppression_key": sk}
        handler = CUSTOMER_HANDLERS.get(kind, c_generic)
        send_as = "merchant_on_behalf"
    else:
        handler = MERCHANT_HANDLERS.get(kind, h_generic)
        send_as = "vera"
    msg = handler(f)
    body = _scrub(msg.body, f.taboos())
    tname = f"{'merchant' if is_customer else 'vera'}_{kind or 'generic'}_v1"
    params = [str(x) for x in (msg.params or []) if x not in (None, "")][:5]
    return {
        "skip": False,
        "body": body,
        "cta": msg.cta,
        "send_as": send_as,
        "suppression_key": sk,
        "rationale": msg.rationale,
        "template_name": tname,
        "template_params": params,
        "known_kind": kind in KNOWN_KINDS,
    }
