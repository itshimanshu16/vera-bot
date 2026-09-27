# Vera Bot — Technical & Submission Specification

**Candidate:** Himanshu Gautam  
**Institution:** Delhi Technological University (DTU)  
**Email:** `himanshugautam_23pe029@dtu.ac.in`  
**Public Repository:** [https://github.com/itshimanshu16/vera-bot](https://github.com/itshimanshu16/vera-bot)  
**Live Endpoint:** [https://vera-bot-magicpic.onrender.com](https://vera-bot-magicpic.onrender.com)  

---

## 1. Executive Summary

**Vera** is an autonomous AI merchant relationship manager built for **magicpin**. It proactively identifies merchant opportunities (performance spikes, dips, seasonal demand, reviews, lapsed customers, compliance updates) and communicates via targeted WhatsApp messages.

The system is built on a **deterministic first principles architecture**:
- **Zero Hallucination:** Every number, offer, name, and date cited in messages is derived directly from verified contexts (`category`, `merchant`, `customer`, `trigger`). It never invents discounts or facts.
- **Strict Conversational Standards:** Exactly one Call-to-Action (CTA) per message, zero URLs, and a maximum of 1 message per merchant per tick.
- **Stateful Multi-Turn Engine:** Handles merchant auto-replies, commits into immediate draft actions, gracefully exits on opt-outs/hostile messages, and redirects off-topic queries.
- **High Performance:** Deterministic execution allows sub-50ms local responses and ~1s cloud latency, comfortably beating the judge's strict timeouts.

---

## 2. API Contract & Endpoints

The service strictly adheres to magicpin's Vera API specification:

| Method | Endpoint | Description | Sample Output / Behavior |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/healthz` | Health check & uptime | `{"status": "ok", "uptime_seconds": 120, "contexts_loaded": {...}}` |
| `GET` | `/v1/metadata` | Team information & bot architecture summary | Returns candidate details, approach, model description, contact email. |
| `POST` | `/v1/context` | Versioned context ingestion (`category`, `merchant`, `customer`, `trigger`) | Stores state with monotonic version tracking; rejects stale versions with HTTP 409. |
| `POST` | `/v1/tick` | Proactive engagement engine | Evaluates available triggers, selects highest priority per merchant, composes message. |
| `POST` | `/v1/reply` | Reactive conversational engine | Multi-turn message state machine (auto-reply detection, commitment switch, opt-out). |
| `POST` | `/v1/teardown` | Test state reset | Wipes in-memory and persisted contexts for fresh evaluation runs. |

---

## 3. Core Architecture & Modules

The repository is structured into modular components:

```
vera-bot/
├── app.py                 # FastAPI server exposing all /v1 endpoints
├── vera/
│   ├── composer.py        # Trigger handlers & message drafting logic
│   ├── facts.py           # Context extraction & fact verification
│   ├── replies.py         # Multi-turn conversational state machine
│   ├── store.py           # Thread-safe in-memory versioned context store
│   ├── util.py            # Date math, ISO formatters, text helpers
│   └── llm.py             # Optional LLM rewrite & number validator
├── tests/
│   ├── harness.py         # Complete end-to-end simulation runner
│   └── dump_all.py        # Message preview & audit tool across all triggers
├── requirements.txt       # Dependencies (fastapi, uvicorn, pydantic)
└── render.yaml            # Render deployment blueprint
```

### Key Modules:

### `vera/composer.py`
Contains custom, category-aware handlers for every trigger kind:
- **Performance Dips & Spikes:** Contextualizes 7-day metric shifts, pre-empts seasonal anxiety, and offers concrete next steps (e.g., promotional drafts).
- **Competitor Events:** Leans on the merchant's unique strengths rather than engaging in destructive price-matching.
- **Customer Recalls & Lapsed Members:** Tailors tone by category (e.g., gentle health recall for dentists/pharmacy, zero-shame friendly nudge for gyms/salons).
- **Festival & Seasonal Windows:** Long-range festival notices avoid premature discounts and focus on pre-booking lock-ins.
- **Language Adaptation:** Generates natural English or Hinglish based on merchant and customer language preferences.

### `vera/replies.py`
Maintains conversational context across turns:
- **Auto-Reply Ladder:**
  - Turn 1: Friendly greeting + acknowledges possible auto-reply.
  - Turn 2: Enters 24-hour wait (`wait_seconds: 86400`).
  - Turn 3: Permanently ends dialogue to respect merchant boundary (`action: "end"`).
- **Commitment Transition:**
  - When merchant says "yes", "lets do it", or confirms, immediately switches from pitching to **ACTION** mode (delivers draft, Google post, or booking confirmation). No redundant qualifying questions.
- **Hostile / Stop Detection:**
  - Immediate `action: "end"` upon opt-out ("stop", "unsubscribe", "not interested", "spam").
- **Off-Topic Deflection:**
  - Politely addresses unrelated questions and pivots back to the active proposal.

### `vera/store.py`
- Stores categories, merchants, customers, and triggers in memory.
- Enforces optimistic concurrency via `version`: updates are accepted only if `incoming_version > current_version`.
- Persists state across server recycles using atomic file writes (`vera_state.json`).

---

## 4. Evaluation & Test Results

The bot underwent validation through both the internal test harness and magicpin's official LLM Judge Simulator:

### 1. Internal Harness (`tests/harness.py`)
- Evaluated against all 100 triggers across 5 business categories (Dentists, Gyms, Pharmacies, Restaurants, Salons).
- **Result:** `No problems found.` All latencies `< 100ms` locally.
- **Determinism:** 100% verified across duplicate runs.

### 2. Official Judge Simulator (`magicpin-ai-challenge/judge_simulator.py`)
- **4 Official Scenario Tests:**
  - `warmup`: **PASS**
  - `auto_reply`: **PASS**
  - `intent`: **PASS**
  - `hostile`: **PASS**
- **Full 14-Message Multi-Dimensional Evaluation:**
  - **Overall Score:** **40 / 50 (80%) — EXCELLENT**
  - **Avg Specificity:** `8 / 10`
  - **Avg Category Fit:** `8 / 10`
  - **Avg Merchant Fit:** `8 / 10`
  - **Avg Decision Quality:** `8 / 10`
  - **Avg Engagement:** `8 / 10`

---

## 5. Live Deployment & Operational Setup

- **Host:** Render Free Tier (Web Service)
- **Runtime:** Python 3.11.9
- **Command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
- **Health Check:** `/v1/healthz`
- **Uptime Monitoring:** Configured via `cron-job.org` with an automated ping every 5 minutes to prevent idle sleep and cold starts.
- **Initial State:** Wiped clean via `POST /v1/teardown`, ready for the magicpin evaluation judge.

---

## 6. Submission Details

- **Submission Portal:** `magicpin.com/vera/ai-challenge`
- **Registered Email:** `himanshugautam_23pe029@dtu.ac.in`
- **Base URL for Evaluation:**
  ```
  https://vera-bot-magicpic.onrender.com
  ```
