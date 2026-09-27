"""Versioned, thread-safe context store + conversation/suppression state.

Everything the judge pushes lives here. Contexts are keyed by (scope, context_id)
and only a strictly higher version replaces what we hold. State is snapshotted to
a JSON file (best-effort) so an accidental process restart does not lose context.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

SCOPES = ("category", "merchant", "customer", "trigger")


class Store:
    def __init__(self, path: Optional[str] = None):
        self.lock = threading.RLock()
        self.path = path
        self.contexts: dict[str, dict[str, dict]] = {s: {} for s in SCOPES}
        # conversation_id -> state dict (see replies.py)
        self.conversations: dict[str, dict] = {}
        # suppression keys already used for a send
        self.sent_keys: set[str] = set()
        # trigger ids already acted upon
        self.sent_triggers: set[str] = set()
        # hash(recipient, body) of every message delivered — blocks verbatim repeats
        self.sent_bodies: set[str] = set()
        # triggers the judge listed as active but we deferred (per-merchant cap)
        self.deferred: list[str] = []
        # merchant_id -> {"opted_out": bool, "auto_replies": {text: count}, "auto_total": int}
        self.merchant_state: dict[str, dict] = {}
        # customer_id -> {"opted_out": bool}
        self.customer_state: dict[str, dict] = {}
        self.session: dict[str, Any] = {}
        self._dirty = False
        self._last_save = 0.0
        if path:
            self._load()

    # ------------------------------------------------------------------ contexts
    def put(self, scope: str, cid: str, version: int, payload: dict) -> tuple[bool, int]:
        """Returns (accepted, current_version)."""
        with self.lock:
            cur = self.contexts[scope].get(cid)
            if cur is not None and cur["version"] >= version:
                return False, cur["version"]
            self.contexts[scope][cid] = {"version": version, "payload": payload,
                                         "stored_at": time.time()}
            self._dirty = True
        self.save_soon()
        return True, version

    def get(self, scope: str, cid: Optional[str]) -> Optional[dict]:
        if not cid:
            return None
        with self.lock:
            rec = self.contexts.get(scope, {}).get(cid)
            return rec["payload"] if rec else None

    def version(self, scope: str, cid: str) -> int:
        with self.lock:
            rec = self.contexts.get(scope, {}).get(cid)
            return rec["version"] if rec else 0

    def counts(self) -> dict[str, int]:
        with self.lock:
            return {s: len(self.contexts[s]) for s in SCOPES}

    def all(self, scope: str) -> dict[str, dict]:
        with self.lock:
            return {k: v["payload"] for k, v in self.contexts[scope].items()}

    # ------------------------------------------------------------------ merchant / customer flags
    def mstate(self, merchant_id: str) -> dict:
        with self.lock:
            return self.merchant_state.setdefault(
                merchant_id or "_unknown", {"opted_out": False, "auto_replies": {}, "auto_total": 0})

    def cstate(self, customer_id: str) -> dict:
        with self.lock:
            return self.customer_state.setdefault(customer_id, {"opted_out": False})

    def new_session(self):
        """Forget per-run send state (dedup, deferrals, opt-outs, conversations); keep contexts."""
        with self.lock:
            self.conversations.clear()
            self.sent_keys.clear()
            self.sent_triggers.clear()
            self.sent_bodies.clear()
            self.deferred.clear()
            self.merchant_state.clear()
            self.customer_state.clear()
            self._dirty = True

    # ------------------------------------------------------------------ persistence
    def wipe(self):
        with self.lock:
            self.contexts = {s: {} for s in SCOPES}
            self.conversations.clear()
            self.sent_keys.clear()
            self.sent_triggers.clear()
            self.sent_bodies.clear()
            self.deferred.clear()
            self.merchant_state.clear()
            self.customer_state.clear()
            self.session.clear()
            self._dirty = True
        self.save(force=True)

    def save_soon(self):
        # Debounced: at most one disk write per second.
        if time.time() - self._last_save > 1.0:
            self.save()

    def save(self, force: bool = False):
        if not self.path:
            return
        with self.lock:
            if not (self._dirty or force):
                return
            data = {
                "contexts": self.contexts,
                "conversations": self.conversations,
                "sent_keys": sorted(self.sent_keys),
                "sent_triggers": sorted(self.sent_triggers),
                "sent_bodies": sorted(self.sent_bodies),
                "deferred": self.deferred,
                "merchant_state": self.merchant_state,
                "customer_state": self.customer_state,
                "session": self.session,
            }
            self._dirty = False
            self._last_save = time.time()
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except Exception:
                self._dirty = True

    def mark_dirty(self):
        with self.lock:
            self._dirty = True

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        with self.lock:
            for s in SCOPES:
                self.contexts[s] = data.get("contexts", {}).get(s, {})
            self.conversations = data.get("conversations", {})
            self.sent_keys = set(data.get("sent_keys", []))
            self.sent_triggers = set(data.get("sent_triggers", []))
            self.sent_bodies = set(data.get("sent_bodies", []))
            self.deferred = data.get("deferred", [])
            self.merchant_state = data.get("merchant_state", {})
            self.customer_state = data.get("customer_state", {})
            self.session = data.get("session", {})
