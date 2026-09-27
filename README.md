# Vera bot — magicpin AI Challenge
**Shaurya Bhardwaj** · FastAPI · all 5 endpoints (+ optional `/v1/teardown`)

## Approach: rules decide, facts only, LLM optional
1. **One signal per message.** Each trigger kind maps to a handler (28 handlers covering 42 kind names, plus a generic fallback). The handler picks the single fact that explains *why now*: a digest item with its source and trial size, a 7-day delta, a gap versus peers, a review theme, a batch number, and so on. It then adds **one** likely cause and **one** fix. It never lists every fact.
2. **Placeholder triggers still get real messages.** 70 of the 100 dataset triggers have empty payloads, so each handler falls back to the merchant's own data. It uses the worst `delta_7d`, then the largest gap versus `peer_stats`, then signals (unverified, no live offer, stale posts), review themes and seasonal beats for the current month. If the data doesn't have something (a competitor's name, a festival, a review theme), the message is written without it and nothing is made up.
3. **Grounding rules.**
   - Every number comes from the contexts or from simple arithmetic on them (for example, "₹100 under your ₹299").
   - Category catalog offers appear only as *suggestions* to the merchant. Customers are only ever quoted the merchant's own live offers.
   - The bot never sends URLs, strips taboo words, and turns jargon (GBP) into plain words.
4. **Voice.**
   - Dentists get a peer-clinical tone with "Dr." and no Hinglish.
   - Salons, restaurants, gyms and pharmacies get their own register, emoji and vocabulary.
   - Merchants who list Hindi before any regional language get a light Hinglish closing line.
   - Customer messages follow the customer's `language_pref`: `hi`, `hi-en mix`, or a regional greeting (Vanakkam, Namaskaram).
5. **Judgment, not templating.** Examples:
   - On a Saturday IPL match: skip the dine-in promo, push delivery, and keep the Tue–Thu BOGO for weeknights.
   - Diwali 188 days out: too early for a discount, so open pre-bookings instead.
   - A seasonal gym dip: reframe it and protect the member base.
   - A competitor that is ₹100 cheaper: don't match the price; lean on the review strength.
   - A refill that includes atorvastatin: check the batch against this month's recall.
6. **Customer-facing messages.** They go out as `merchant_on_behalf` and are sent only when consent is on file. They use the real slots from the payload, never invented ones.
7. **Tick planner.**
   - Priority is urgency first, then a weight per kind.
   - At most **one message per merchant per tick**; the rest wait for the next tick, as restraint rather than spam.
   - At most 20 actions per tick.
   - Duplicates are blocked by `suppression_key` and by identical body per recipient.
   - A new judge run is detected when the simulated clock moves backwards or after more than 15 minutes of silence, so dedup state from an earlier run never mutes the evaluation.
8. **Reply engine.** Deterministic classes are checked in this order: auto-reply, opt-out, hostile, customer slot choice, off-topic, accept, objection, decline-later, decline, thanks, question.
   - **Auto-reply:** tracked *per merchant across conversations*. The first time, one owner-flag nudge; the second time, wait 24h; the third time, end.
   - **Intent:** "yes / let's do it / what's next" switches straight to action mode. The bot delivers the actual artifact (abstract plus patient WhatsApp, recall-customer note, review replies, office-admin WhatsApp…) and then one CONFIRM.
   - **Opt-out:** ends immediately and suppresses that merchant.
   - **Hostile without an opt-out:** one apology plus an easy way back.
   - **Off-topic (GST…):** polite decline, then back to the open thread.
   - **Customers:** "2" books slot 2; a reschedule request is acknowledged without inventing a slot.
   - **Repeats:** a repeated body is never sent.

## Model choice
- The default is the **pure rules engine**: deterministic, about 10 ms per tick, no quota or timeout risk, and no possibility of hallucination.
- Setting `LLM_API_KEY` turns on an optional layer (Gemini 2.5 Flash by default; OpenAI, Groq, OpenRouter, DeepSeek and Anthropic are also supported). It runs at temperature 0 and caches by input hash. In `smart` mode it writes only for trigger kinds the rules don't know, plus free-form reply turns. In `all` mode it polishes everything.
- **Every LLM output is validated.** Each number must exist in the input contexts, at least 60% of the draft's facts must be kept, and there must be no URL or taboo word. The name must be present, with at most two question marks. Anything that fails falls back to the rules draft, and so does anything that misses the 9 s tick budget.

## Tradeoffs
- Rules give up some fluency on unseen trigger kinds in exchange for zero fabrication and full determinism. The LLM fallback is there to cover that fluency gap.
- One message per merchant per tick means a merchant with four triggers gets them over four ticks, not in one burst.
- Customer triggers with no consent record, or with no customer context, are not sent.

## What additional context would have helped
The merchant's real open slots, prices for services that aren't in an offer, review text (not just themes), and sent/read receipts. With those, cadence could be planned rather than inferred.

## Run & test
```bash
pip install -r requirements.txt
uvicorn app:app --port 8080
python tests/harness.py http://localhost:8080 ../magicpin-ai-challenge/expanded   # full phase 1-4 simulation
python tests/dump_all.py ../magicpin-ai-challenge/expanded                         # every composed message
python tests/make_submission.py ../magicpin-ai-challenge/expanded                  # submission.jsonl (30 pairs)
```
