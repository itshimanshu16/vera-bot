"""Fact extraction: turns raw contexts into small, verified facts the composer can use.

Nothing here invents data. Every value returned is read from (or arithmetically
derived from) the category / merchant / trigger / customer payloads.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .util import (pretty_pref, ctr_str, fint, humanize, month_of, month_range_contains, months_until_range,
                   num, pct)

CAT_NOUN = {  # singular noun, plural customer word, plural noun
    "dentists": ("clinic", "patients", "clinics"),
    "salons": ("salon", "clients", "salons"),
    "restaurants": ("restaurant", "diners", "restaurants"),
    "gyms": ("gym", "members", "gyms"),
    "pharmacies": ("pharmacy", "customers", "pharmacies"),
}


def cat_words(slug: str) -> tuple[str, str, str]:
    return CAT_NOUN.get(slug, ("business", "customers", "business"))


class Facts:
    """Convenience wrapper around the four contexts."""

    def __init__(self, category: dict, merchant: dict, trigger: dict,
                 customer: Optional[dict] = None, now: Optional[str] = None):
        self.category = category or {}
        self.merchant = merchant or {}
        self.trigger = trigger or {}
        self.customer = customer
        self.now = now
        self.slug = self.category.get("slug") or self.merchant.get("category_slug") or ""
        self.payload = self.trigger.get("payload") or {}
        self.perf = self.merchant.get("performance") or {}
        self.peer = self.category.get("peer_stats") or {}
        self.agg = self.merchant.get("customer_aggregate") or {}
        self.ident = self.merchant.get("identity") or {}
        self.sub = self.merchant.get("subscription") or {}
        self.noun, self.people, self.nouns = cat_words(self.slug)

    # ---------------------------------------------------------------- basic
    @property
    def locality(self) -> str:
        return str(self.ident.get("locality") or self.ident.get("city") or "").strip()

    @property
    def city(self) -> str:
        return str(self.ident.get("city") or "").strip()

    def placeholder(self) -> bool:
        return bool(self.payload.get("placeholder")) or not self.payload

    # ---------------------------------------------------------------- signals
    def signals(self) -> dict[str, str]:
        out = {}
        for s in self.merchant.get("signals") or []:
            s = str(s)
            if ":" in s:
                k, v = s.split(":", 1)
                out[k.strip()] = v.strip()
            else:
                out[s.strip()] = ""
        return out

    # ---------------------------------------------------------------- offers
    def active_offers(self) -> list[str]:
        return [str(o.get("title")) for o in self.merchant.get("offers") or []
                if str(o.get("status", "")).lower() == "active" and o.get("title")]

    def expired_offers(self) -> list[str]:
        return [str(o.get("title")) for o in self.merchant.get("offers") or []
                if str(o.get("status", "")).lower() in ("expired", "paused") and o.get("title")]

    def catalog(self) -> list[dict]:
        return list(self.category.get("offer_catalog") or [])

    def catalog_type(self, title: str) -> str:
        t = (title or "").lower()
        for o in self.catalog():
            if str(o.get("title", "")).lower() == t:
                return str(o.get("type", ""))
        if "free" in t:
            return "free_service"
        if "₹" in t or "@" in t:
            return "service_at_price"
        return ""

    def free_active_offer(self) -> Optional[str]:
        for t in self.active_offers():
            if "free" in t.lower():
                return t
        return None

    def price_active_offer(self) -> Optional[str]:
        for t in self.active_offers():
            if "₹" in t:
                return t
        return None

    def premium_active_offer(self) -> Optional[str]:
        best, bp = None, -1.0
        for t in self.active_offers():
            m = re.search(r"₹\s?([\d,]+)", t)
            v = float(m.group(1).replace(",", "")) if m else -1.0
            if v > bp:
                best, bp = t, v
        return best

    def best_active_offer(self) -> Optional[str]:
        offs = self.active_offers()
        if not offs:
            return None
        return self.price_active_offer() or offs[0]

    def suggested_catalog_offer(self, purpose: str = "acquire") -> Optional[str]:
        """Pick a category catalog offer (service+price preferred) that fits this merchant.
        Only ever presented as a *suggestion*, never as the merchant's existing offer."""
        cat = self.catalog()
        if not cat:
            return None
        name = str(self.ident.get("name", "")).lower()
        active = {a.lower() for a in self.active_offers()}
        no_delivery = "delivery_not_set_up" in self.signals()
        cat = [o for o in cat if not (no_delivery and "deliver" in str(o.get("title", "")).lower())] or cat
        prefs: list[str] = []
        if self.slug == "restaurants":
            if "pizza" in name:
                prefs = ["pizza"]
            elif any(w in name for w in ("thali", "madras", "south", "veg", "mylari")):
                prefs = ["thali"]
            elif any(w in name for w in ("cafe", "chai", "diner", "family")):
                prefs = ["brunch", "thali"]
            else:
                prefs = ["match-night", "combo"]
        elif self.slug == "dentists":
            prefs = {"winback": ["consultation", "cleaning"], "festive": ["whitening"]}.get(purpose, ["cleaning"])
        elif self.slug == "salons":
            prefs = {"premium": ["hair spa", "keratin"], "festive": ["hair spa", "bridal"]}.get(purpose, ["haircut"])
        elif self.slug == "gyms":
            prefs = {"retain": ["body composition", "refer"], "festive": ["couple", "combo"]}.get(purpose, ["trial"])
        elif self.slug == "pharmacies":
            prefs = {"health": ["bp", "sugar"], "festive": ["diabetic", "bp"]}.get(purpose, ["delivery", "bp"])
        for p in prefs:
            for o in cat:
                t = str(o.get("title", ""))
                if p in t.lower() and t.lower() not in active:
                    return t
        for o in cat:
            if o.get("type") == "service_at_price" and str(o.get("title", "")).lower() not in active:
                return str(o.get("title"))
        return str(cat[0].get("title"))

    # ---------------------------------------------------------------- performance
    def delta(self, metric: str) -> Optional[float]:
        d = self.perf.get("delta_7d") or {}
        return num(d.get(f"{metric}_pct"))

    def worst_delta(self) -> Optional[tuple[str, float]]:
        cands = [(m, self.delta(m)) for m in ("calls", "views", "ctr")]
        cands = [(m, v) for m, v in cands if v is not None and v < 0]
        if not cands:
            return None
        return min(cands, key=lambda x: (x[1], x[0]))

    def best_delta(self) -> Optional[tuple[str, float]]:
        cands = [(m, self.delta(m)) for m in ("calls", "views", "ctr")]
        cands = [(m, v) for m, v in cands if v is not None and v > 0]
        if not cands:
            return None
        return max(cands, key=lambda x: (x[1], x[0]))

    def peer_gap(self, metric: str) -> Optional[tuple[float, float]]:
        """(merchant_value, peer_value) for views/calls/directions/ctr."""
        mv = num(self.perf.get(metric))
        key = "avg_ctr" if metric == "ctr" else f"avg_{metric}_30d"
        pv = num(self.peer.get(key))
        if mv is None or pv is None or pv == 0:
            return None
        return mv, pv

    def below_peer(self) -> list[tuple[str, float, float, float]]:
        """Metrics where the merchant trails peers, sorted by relative gap."""
        out = []
        for m in ("calls", "views", "ctr", "directions"):
            g = self.peer_gap(m)
            if g and g[0] < g[1]:
                out.append((m, g[0], g[1], (g[1] - g[0]) / g[1]))
        return sorted(out, key=lambda x: (-x[3], x[0]))

    def above_peer(self) -> list[tuple[str, float, float, float]]:
        out = []
        for m in ("calls", "views", "ctr", "directions"):
            g = self.peer_gap(m)
            if g and g[0] > g[1]:
                out.append((m, g[0], g[1], (g[0] - g[1]) / g[1]))
        return sorted(out, key=lambda x: (-x[3], x[0]))

    @staticmethod
    def metric_label(m: str) -> str:
        return {"calls": "calls", "views": "profile views", "ctr": "click-through rate",
                "directions": "direction requests", "leads": "leads",
                "review_count": "Google reviews"}.get(m, humanize(m))

    def metric_value(self, m: str, v: float) -> str:
        return ctr_str(v) if m == "ctr" else fint(v)

    # ---------------------------------------------------------------- digest / category knowledge
    def digest_item(self, item_id: Optional[str]) -> Optional[dict]:
        if not item_id:
            return None
        for d in self.category.get("digest") or []:
            if d.get("id") == item_id:
                return d
        return None

    def digest_for_trigger(self) -> Optional[dict]:
        p = self.payload
        for k in ("top_item_id", "digest_item_id", "alert_id", "item_id", "digest_id"):
            it = self.digest_item(p.get(k))
            if it:
                return it
        top = p.get("top_item")
        if isinstance(top, dict) and top.get("title"):
            return top
        return None

    def pick_digest(self, kinds: list[str]) -> Optional[dict]:
        """Deterministically choose the most useful digest item of the given kinds,
        preferring items carrying hard numbers and relevance to this merchant."""
        items = list(self.category.get("digest") or [])
        if not items:
            return None
        order = {k: i for i, k in enumerate(kinds)}
        blob = " ".join(self.signals().keys()).lower()

        def score(d):
            k = str(d.get("kind", ""))
            kind_rank = order.get(k, len(order) + 1)
            text = f"{d.get('title','')} {d.get('summary','')}"
            has_num = 1 if re.search(r"\d+\s?%|\d{2,}", text) else 0
            rel = 0
            seg = str(d.get("patient_segment", ""))
            if seg and self.segment_count(seg) is not None:
                rel = 1
            if "calls" in text.lower() and ("calls" in blob or self.below_peer()):
                rel = max(rel, 1)
            return (kind_rank, -rel, -has_num, str(d.get("id")))

        return sorted(items, key=score)[0]

    def segment_count(self, segment: str) -> Optional[tuple[str, float]]:
        """Map a digest patient_segment (e.g. 'high_risk_adults') to a customer_aggregate count."""
        seg = str(segment or "").lower().rstrip("s")
        toks = [t for t in re.split(r"[_\s-]+", seg) if t]
        best = None
        for k, v in self.agg.items():
            kl = k.lower()
            if num(v) is None or "pct" in kl:
                continue
            hits = sum(1 for t in toks if t.rstrip("s") in kl)
            if hits >= max(1, len(toks) - 1):
                if best is None or hits > best[0]:
                    best = (hits, k, num(v))
        if best:
            return best[1], best[2]
        return None

    def trend_signal(self, prefer: Optional[str] = None) -> Optional[dict]:
        ts = list(self.category.get("trend_signals") or [])
        if not ts:
            return None
        if prefer:
            for t in ts:
                if prefer.lower() in str(t.get("query", "")).lower():
                    return t
        return sorted(ts, key=lambda t: (-(num(t.get("delta_yoy")) or 0), str(t.get("query"))))[0]

    def trend_for_merchant(self) -> Optional[dict]:
        """Trend signal most related to the merchant's offers / name, else the strongest."""
        words = " ".join(self.active_offers() + [str(self.ident.get("name", ""))]).lower()
        best = None
        for t in self.category.get("trend_signals") or []:
            q = str(t.get("query", "")).lower()
            toks = [w for w in re.split(r"\W+", q) if len(w) > 3 and w not in ("near", "price", "delhi", "cost")]
            if any(w in words for w in toks):
                if best is None or (num(t.get("delta_yoy")) or 0) > (num(best.get("delta_yoy")) or 0):
                    best = t
        return best or self.trend_signal()

    def seasonal_beat(self, keywords: Optional[list[str]] = None, horizon: int = 12) -> Optional[dict]:
        beats = list(self.category.get("seasonal_beats") or [])
        month = month_of(self.now)
        cands = []
        for b in beats:
            rng = str(b.get("month_range", ""))
            note = str(b.get("note", "")).lower()
            if keywords and not any(re.search(r"\b" + re.escape(k) + r"\b", note) for k in keywords):
                continue
            mu = months_until_range(rng, month)
            if mu is None or mu > horizon:
                continue
            cands.append((mu, rng, b))
        if not cands:
            return None
        return sorted(cands, key=lambda x: (x[0], x[1]))[0][2]

    def current_beat(self) -> Optional[dict]:
        month = month_of(self.now)
        for b in self.category.get("seasonal_beats") or []:
            if month_range_contains(str(b.get("month_range", "")), month):
                return b
        return None

    def taboos(self) -> list[str]:
        v = self.category.get("voice") or {}
        return [str(t) for t in (v.get("vocab_taboo") or v.get("taboos") or [])]

    # ---------------------------------------------------------------- history
    def last_merchant_msg(self) -> Optional[dict]:
        for h in reversed(self.merchant.get("conversation_history") or []):
            if str(h.get("from", "")).lower() == "merchant":
                return h
        return None

    def last_vera_msg(self) -> Optional[dict]:
        for h in reversed(self.merchant.get("conversation_history") or []):
            if str(h.get("from", "")).lower() == "vera":
                return h
        return None

    def history_mentions(self, word: str) -> Optional[dict]:
        for h in reversed(self.merchant.get("conversation_history") or []):
            if word.lower() in str(h.get("body", "")).lower():
                return h
        return None

    def review_theme(self, sentiment: Optional[str] = None) -> Optional[dict]:
        themes = list(self.merchant.get("review_themes") or [])
        if sentiment:
            themes = [t for t in themes if str(t.get("sentiment", "")).startswith(sentiment)]
        if not themes:
            return None
        return sorted(themes, key=lambda t: (-(num(t.get("occurrences_30d")) or 0), str(t.get("theme"))))[0]

    # ---------------------------------------------------------------- customer
    def consent_ok(self) -> tuple[bool, str]:
        c = self.customer
        if not c:
            return False, "no customer context"
        cst = c.get("consent") or {}
        prefs = c.get("preferences") or {}
        if not cst.get("opted_in_at") or not cst.get("scope"):
            return False, "customer has no recorded opt-in"
        if prefs.get("reminder_opt_in") is False:
            return False, "customer opted out of reminders"
        if str(prefs.get("channel", "")).lower() in ("none_recorded", "none"):
            return False, "no messaging channel recorded"
        return True, "consent on file: " + ", ".join(map(str, cst.get("scope") or []))

    def customer_rel(self) -> dict:
        return (self.customer or {}).get("relationship") or {}

    def customer_prefs(self) -> dict:
        return (self.customer or {}).get("preferences") or {}

    def slot_pref_text(self) -> str:
        s = str(self.customer_prefs().get("preferred_slots") or "")
        return pretty_pref(s)
