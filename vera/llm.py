"""Optional LLM layer. The bot is fully functional without it.

Env vars:
  LLM_PROVIDER  gemini | openai | groq | openrouter | deepseek | anthropic   (default: gemini)
  LLM_API_KEY   your key (if empty -> LLM disabled)
  LLM_MODEL     model id (sensible default per provider)
  LLM_MODE      off | smart | all     (default: smart)
                  smart = LLM writes messages only for trigger kinds the rules engine doesn't
                          know + free-form reply turns (questions/objections)
                  all   = also polishes every rules-engine draft
  LLM_TIMEOUT   seconds per call (default 8)

Every LLM output is validated; on any failure the deterministic draft is used:
  * every number in the output must exist in the input contexts or the draft
  * no URLs, no category taboo words
  * the salutation / name must be kept, one CTA at most, length bounded
Outputs are cached by input hash -> same input, same output (determinism).
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Optional
from urllib import request as urlrequest

from .util import stable_hash

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openai/gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "anthropic": "claude-haiku-4-5",
}

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
URL_RE = re.compile(r"https?://|www\.", re.I)


def _nums(text: str) -> set[str]:
    out = set()
    for m in NUM_RE.findall(text or ""):
        v = m.replace(",", "").rstrip(".")
        if not v:
            continue
        try:
            fv = float(v)
        except ValueError:
            continue
        out.add(f"{fv:g}")
    return out


def allowed_numbers(*objs: Any) -> set[str]:
    """All numbers that appear anywhere in the given objects, plus their % forms."""
    allowed: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(k)
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, bool) or o is None:
            return
        elif isinstance(o, (int, float)):
            allowed.add(f"{float(o):g}")
            if abs(o) <= 1.5:
                allowed.add(f"{round(abs(o) * 100, 1):g}")
                allowed.add(f"{round(abs(o) * 100):g}")
            allowed.add(f"{abs(float(o)):g}")
        else:
            allowed.update(_nums(str(o)))

    for o in objs:
        walk(o)
    allowed.update({str(i) for i in range(0, 11)})
    return allowed


class LLM:
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        self.key = os.getenv("LLM_API_KEY", "").strip()
        self.model = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODELS.get(self.provider, "")
        self.mode = os.getenv("LLM_MODE", "smart").strip().lower()
        self.timeout = float(os.getenv("LLM_TIMEOUT", "8"))
        self.enabled = bool(self.key) and self.mode != "off"
        self.cache: dict[str, Optional[str]] = {}
        self.lock = threading.Lock()
        self.stats = {"calls": 0, "ok": 0, "rejected": 0, "errors": 0}

    def describe(self) -> str:
        return f"{self.provider}:{self.model} ({self.mode})" if self.enabled else "none (deterministic rules engine)"

    # ------------------------------------------------------------------ transport
    def _call(self, system: str, user: str) -> str:
        p = self.provider
        if p == "gemini":
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "topP": 1, "maxOutputTokens": 700},
            }
            if "2.5" in self.model and "pro" not in self.model:
                body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.key}"
            data = self._post(url, body, {})
            return data["candidates"][0]["content"]["parts"][0]["text"]
        if p == "anthropic":
            body = {"model": self.model, "max_tokens": 700, "temperature": 0, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            data = self._post("https://api.anthropic.com/v1/messages", body,
                              {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
            return data["content"][0]["text"]
        base = {"openai": "https://api.openai.com/v1", "groq": "https://api.groq.com/openai/v1",
                "openrouter": "https://openrouter.ai/api/v1", "deepseek": "https://api.deepseek.com/v1"}.get(p)
        if not base:
            raise ValueError(f"unknown provider {p}")
        body = {"model": self.model, "temperature": 0, "max_tokens": 700, "seed": 7,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        data = self._post(base + "/chat/completions", body, {"Authorization": f"Bearer {self.key}"})
        return data["choices"][0]["message"]["content"]

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        h = {"Content-Type": "application/json"}
        h.update(headers)
        req = urlrequest.Request(url, data=json.dumps(body).encode("utf-8"), headers=h, method="POST")
        with urlrequest.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _cached(self, key: str, fn) -> Optional[str]:
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        try:
            self.stats["calls"] += 1
            out = fn()
        except Exception:
            self.stats["errors"] += 1
            return None  # not cached: a transient error may succeed next time
        with self.lock:
            self.cache[key] = out
        return out

    # ------------------------------------------------------------------ validation
    def validate(self, text: Optional[str], draft: str, allowed: set[str], taboos: list[str],
                 must_have: Optional[str], max_len: int) -> Optional[str]:
        if not text:
            return None
        t = text.strip().strip('"').strip()
        t = re.sub(r"^```\w*|```$", "", t).strip()
        if not t or len(t) > max_len or URL_RE.search(t):
            return None
        low = t.lower()
        for tb in taboos:
            base = re.sub(r"\s*\(.*?\)", "", tb).strip().lower()
            if base and base in low:
                return None
        bad = [n for n in _nums(t) if n not in allowed]
        if bad:
            return None
        dn = _nums(draft) - {str(i) for i in range(0, 11)}
        if dn:
            kept = len(dn & _nums(t)) / len(dn)
            if kept < 0.6:
                return None
        if must_have and must_have.lower() not in low:
            return None
        if t.count("?") > 2:
            return None
        return t

    # ------------------------------------------------------------------ compose
    def compose(self, f, draft: str, kind_known: bool, cta: str) -> Optional[str]:
        if not self.enabled:
            return None
        if self.mode != "all" and kind_known:
            return None
        from .util import salutation
        voice = f.category.get("voice") or {}
        sal = salutation(f.merchant, f.slug)
        facts = {
            "category": {k: f.category.get(k) for k in ("slug", "voice", "peer_stats", "offer_catalog", "seasonal_beats") if f.category.get(k)},
            "digest_item": f.digest_for_trigger(),
            "merchant": {k: f.merchant.get(k) for k in ("identity", "subscription", "performance", "offers",
                                                          "customer_aggregate", "signals", "review_themes") if f.merchant.get(k)},
            "recent_conversation": (f.merchant.get("conversation_history") or [])[-3:],
            "trigger": f.trigger,
            "customer": f.customer,
        }
        is_cust = bool(f.customer)
        system = (
            "You write one WhatsApp message for magicpin's merchant assistant 'Vera'. "
            + ("It is sent ON BEHALF OF the merchant to their own customer. " if is_cust else "It is sent by Vera to the merchant (business owner). ")
            + "Hard rules: (1) use ONLY facts, numbers, names, dates and offers present in FACTS or DRAFT — never invent any; "
            "(2) open with the single most important reason for messaging now; don't list every fact; "
            "(3) exactly one call-to-action, as the last sentence, low-effort (reply YES / CONFIRM / a number); "
            "(4) no URLs, no hype, no greeting preamble, don't introduce yourself; "
            f"(5) tone: {voice.get('tone', 'peer')}, register {voice.get('register', '')}; "
            f"(6) never use: {', '.join(voice.get('vocab_taboo') or [])}; "
            "(7) Indian English; if the recipient's language preference includes Hindi you may use natural Hinglish; "
            "(8) max 600 characters. Output only the message text."
        )
        user = (f"FACTS (JSON):\n{json.dumps(facts, ensure_ascii=False, default=str)[:9000]}\n\n"
                f"DRAFT (correct but plain — improve it, keep its facts and CTA intent):\n{draft}\n\n"
                f"Address the recipient as used in the draft ('{sal}' for merchants). Write the final message:")
        key = "c:" + stable_hash([self.provider, self.model, system, user])
        out = self._cached(key, lambda: self._call(system, user))
        allowed = allowed_numbers(facts, draft)
        from .util import customer_first
        must = customer_first(f.customer)[0] if is_cust else sal.replace("Dr. ", "")
        res = self.validate(out, draft, allowed, f.taboos(), must or None, 700)
        if res:
            self.stats["ok"] += 1
        elif out:
            self.stats["rejected"] += 1
        return res

    # ------------------------------------------------------------------ reply
    def reply(self, f, conv: dict, msg: str, cls: str, action: Optional[str], hi: bool) -> Optional[str]:
        if not self.enabled:
            return None
        turns = conv.get("turns", [])[-6:]
        facts = {
            "merchant": {k: f.merchant.get(k) for k in ("identity", "subscription", "performance", "offers",
                                                          "customer_aggregate", "signals") if f.merchant.get(k)},
            "peer_stats": f.category.get("peer_stats"),
            "trigger": f.trigger,
            "digest_item": f.digest_for_trigger(),
        }
        system = (
            "You are Vera, magicpin's WhatsApp assistant for a merchant. Continue the conversation with ONE short reply (max 350 characters). "
            "Answer the merchant's latest message directly using ONLY facts in FACTS/CONVERSATION — if the answer isn't there, say you'll confirm and come back; never invent numbers, prices, names or dates. "
            "No URLs. Don't re-introduce yourself. Don't ask qualifying questions. End with exactly one low-effort CTA"
            + (f" that moves toward: {action}." if action else ".")
            + (" Reply in natural Hinglish (Roman script)." if hi else " Reply in English.")
            + " Output only the reply text."
        )
        user = (f"FACTS:\n{json.dumps(facts, ensure_ascii=False, default=str)[:7000]}\n\nCONVERSATION:\n"
                + "\n".join(f"{t.get('from')}: {t.get('msg')}" for t in turns)
                + f"\nmerchant (latest, classified as {cls}): {msg}\n\nVera:")
        key = "r:" + stable_hash([self.provider, self.model, system, user])
        out = self._cached(key, lambda: self._call(system, user))
        allowed = allowed_numbers(facts, [t.get("msg") for t in turns], msg)
        res = self.validate(out, "", allowed, f.taboos(), None, 420)
        if res:
            self.stats["ok"] += 1
            prev = {t.get("msg") for t in turns if t.get("from") == "bot"}
            if res in prev:
                return None
        elif out:
            self.stats["rejected"] += 1
        return res
