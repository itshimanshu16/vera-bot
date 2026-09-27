"""Small, pure helpers: number/date formatting, names, language."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Optional

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_IDX = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}


# ---------------------------------------------------------------- numbers
def num(x: Any) -> Optional[float]:
    try:
        if x is None or isinstance(x, bool):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def inr_group(n: float) -> str:
    """Indian digit grouping: 145000 -> 1,45,000 ; 2410 -> 2,410."""
    n = int(round(n))
    neg = n < 0
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + s


def fint(x: Any) -> str:
    v = num(x)
    return inr_group(v) if v is not None else str(x)


def pct(x: Any, signed: bool = False) -> str:
    """0.18 -> '18%'. Values already > 1.5 are treated as percentages."""
    v = num(x)
    if v is None:
        return str(x)
    p = v * 100 if abs(v) <= 1.5 else v
    r = round(p, 1)
    txt = f"{abs(r):g}%"
    if signed:
        return ("+" if r >= 0 else "-") + txt
    return txt


def money(x: Any) -> str:
    v = num(x)
    return f"₹{inr_group(v)}" if v is not None else f"₹{x}"


def ctr_str(x: Any) -> str:
    v = num(x)
    if v is None:
        return str(x)
    return f"{v * 100:.1f}%"


DAY_WORDS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
             "mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def pretty_pref(s: Any) -> str:
    """'saturday_morning' -> 'Saturday morning'; 'weekday_evening' -> 'weekday evening'; 'fri_sat_night' -> 'Fri-Sat night'."""
    toks = [t for t in str(s or "").split("_") if t]
    out = []
    for t in toks:
        out.append(t.capitalize() if t.lower() in DAY_WORDS else t)
    txt = " ".join(out)
    txt = re.sub(r"\b(Fri) (Sat)\b", r"\1-\2", txt)
    txt = re.sub(r"\bafter (\d+)(am|pm)", r"after \1\2", txt)
    return txt


# ---------------------------------------------------------------- dates
def parse_date(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    try:
        if len(t) == 10:
            return datetime.strptime(t, "%Y-%m-%d")
        t = t.replace("Z", "+00:00")
        return datetime.fromisoformat(t)
    except ValueError:
        return None


def nice_date(s: Any, with_day: bool = False, with_year: bool = False) -> str:
    d = parse_date(s)
    if not d:
        return str(s)
    out = f"{d.day} {MONTHS[d.month - 1]}"
    if with_year:
        out += f" {d.year}"
    if with_day:
        out = f"{DAYS[d.weekday()]} {out}"
    return out


def nice_time(s: Any) -> str:
    d = parse_date(s)
    if not d or (d.hour == 0 and d.minute == 0):
        return ""
    h = d.hour % 12 or 12
    ampm = "am" if d.hour < 12 else "pm"
    return f"{h}{ampm}" if d.minute == 0 else f"{h}:{d.minute:02d}{ampm}"


def days_between(a: Any, b: Any) -> Optional[int]:
    da, db = parse_date(a), parse_date(b)
    if not da or not db:
        return None
    return abs((db.date() - da.date()).days)


def month_of(now: Any) -> int:
    d = parse_date(now)
    return d.month if d else datetime.utcnow().month


def month_range_contains(rng: str, month: int) -> bool:
    """'Nov-Feb' contains 12; 'Jan' contains 1; 'Feb 14' contains 2."""
    toks = re.findall(r"[A-Za-z]{3}", rng or "")
    idx = [MONTH_IDX[t.lower()] for t in toks if t.lower() in MONTH_IDX]
    if not idx:
        return False
    if len(idx) == 1:
        return idx[0] == month
    a, b = idx[0], idx[1]
    if a <= b:
        return a <= month <= b
    return month >= a or month <= b


def months_until_range(rng: str, month: int) -> Optional[int]:
    toks = re.findall(r"[A-Za-z]{3}", rng or "")
    idx = [MONTH_IDX[t.lower()] for t in toks if t.lower() in MONTH_IDX]
    if not idx:
        return None
    if month_range_contains(rng, month):
        return 0
    return (idx[0] - month) % 12


# ---------------------------------------------------------------- text
def humanize(s: Any) -> str:
    return str(s or "").replace("_", " ").strip()


def clean_spaces(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" +([,.;:!?])", r"\1", s)
    s = re.sub(r"\.\.+", ".", s)
    return s.strip()


def first_sentence(s: str) -> str:
    s = (s or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    return m.group(1) if m else s


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def pick(options: list, key: str):
    """Deterministic choice among options based on a string key."""
    if not options:
        return None
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


# ---------------------------------------------------------------- names
def owner_first(merchant: dict) -> str:
    ident = merchant.get("identity", {}) or {}
    o = str(ident.get("owner_first_name") or "").strip()
    o = re.sub(r"^(dr\.?\s+)", "", o, flags=re.I).strip()
    return o


def merchant_name(merchant: dict) -> str:
    return str((merchant.get("identity", {}) or {}).get("name") or "your business").strip()


def salutation(merchant: dict, slug: str) -> str:
    first = owner_first(merchant)
    raw = str((merchant.get("identity", {}) or {}).get("owner_first_name") or "")
    is_doc = slug == "dentists" or raw.lower().startswith("dr")
    if first:
        return f"Dr. {first}" if is_doc else first
    name = str((merchant.get("identity", {}) or {}).get("name") or "").strip()
    return name or "Hi there"


GENERIC_FIRST = {"family", "iron", "city", "bright", "crown", "apex", "pearl", "smile", "glow", "active", "fit",
                 "daily", "true", "quick", "reliable", "the", "beauty", "aesthetic", "body", "strength", "pulse",
                 "bend", "madras", "chai", "kabab", "tandoor", "veg", "biryani", "pizza", "sk", "glamour", "dr",
                 "dr.", "asha", "wellness", "healthwell", "sunrise", "apollo", "zen", "karim's", "anita's", "paras",
                 "power", "powerhouse", "studio", "new", "royal", "green", "golden", "sri", "shri"}
BIZ_WORDS = {"salon", "cafe", "pharmacy", "clinic", "studio", "fitness", "dental", "care", "family", "medicos", "gym"}


def short_merchant(merchant: dict) -> str:
    """'Studio11 Family Salon' -> 'Studio11', 'Mylari South Indian Cafe' -> 'Mylari'; else the full name."""
    name = merchant_name(merchant)
    words = name.split()
    if len(words) >= 3 and words[0].lower() not in GENERIC_FIRST and \
            (re.search(r"\d", words[0]) or any(w.lower() in BIZ_WORDS for w in words[1:])):
        return words[0]
    return name


def poss(name: str) -> str:
    """Possessive: 'Sunrise Medicos' -> "Sunrise Medicos'", 'Mylari' -> "Mylari's"."""
    name = name.strip()
    if name.endswith("'s") or name.endswith("’s"):
        return name
    return name + ("'" if name.endswith("s") else "'s")


def customer_first(customer: dict) -> tuple[str, Optional[str]]:
    """Returns (display_name, parent_name). 'Aanya (parent: Sneha)' -> ('Aanya', 'Sneha')."""
    name = str((customer.get("identity", {}) or {}).get("name") or "").strip()
    m = re.match(r"^(.*?)\s*\(parent:\s*([^)]+)\)", name)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if name.startswith("(") or not name:
        return "", None
    return name, None


# ---------------------------------------------------------------- language
REGIONAL = {"ta", "te", "kn", "ml", "mr", "bn", "gu", "pa"}


def merchant_hinglish(merchant: dict) -> bool:
    """True when the merchant lists Hindi before any regional language."""
    langs = [str(l).lower() for l in (merchant.get("identity", {}) or {}).get("languages", []) or []]
    if "hi" not in langs:
        return False
    hi_pos = langs.index("hi")
    for i, l in enumerate(langs):
        if l in REGIONAL and i < hi_pos:
            return False
    return True


def customer_lang(customer: Optional[dict]) -> str:
    """Returns one of: 'en', 'hinglish', 'hi', 'ta', 'te', 'kn' (regional = English + greeting)."""
    if not customer:
        return "en"
    pref = str((customer.get("identity", {}) or {}).get("language_pref") or "en").lower()
    if pref in ("hi", "hindi"):
        return "hi"
    if "hi" in pref and ("en" in pref or "mix" in pref):
        return "hinglish"
    for r in ("ta", "te", "kn"):
        if pref.startswith(r):
            return r
    return "en"


REGIONAL_GREETING = {"ta": "Vanakkam", "te": "Namaskaram", "kn": "Namaskara"}

HINDI_MARKERS = re.compile(
    r"\b(hai|hain|nahi|nahin|kya|karo|kar|karna|haan|han|ji|mujhe|mera|meri|aap|aapka|chahiye|kitna|kitne|"
    r"bhai|accha|acha|theek|thik|bhejo|bata|batao|abhi|baad|mein|hum|humara|kyun|kaise|kab|lekin|par|bhi|"
    r"sab|dekh|dekho|chalega|jaldi|paise|mehenga|mahanga|band|mat|shukriya|dhanyavaad|bhej|yaar|kardo|kijiye|"
    r"batayein|hoga|raha|rahi|nahi|haanji|theek)\b",
    re.I,
)


def looks_hindi(text: str) -> bool:
    if re.search(r"[ऀ-ॿ]", text or ""):
        return True
    return len(HINDI_MARKERS.findall(text or "")) >= 2
