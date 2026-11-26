# FortiSales AI: Project Report

**Project 1: Autonomous AI Sales Voice Agent** · Fortivex AI Round 3

## 1. Business problem
Sales teams lose leads to slow replies and waste time on low-intent visitors. FortiSales AI answers every visitor instantly by text or voice, collects requirements, ranks buying intent as HOT/WARM/COLD and gives the team a summary and dashboard, so reps spend time only on the leads that matter.

## 2. What is built (working prototype)
Flask + SQLite + browser Web Speech API. Optional OpenAI or Gemini for replies; without a key it runs in labelled fallback mode.

| Requirement | Status |
|---|---|
| Voice STT/TTS | Partial: browser mic and spoken replies, turn-based (not streaming); not yet tested in a real browser |
| Contextual memory | Partial: remembers the whole conversation and extracted details within a session; no cross-session memory |
| Multilingual | Partial: voice language picker; AI mode replies in the customer's language; fallback and scoring are English only |
| Lead qualification | Done: explainable point rules, score 0-100, reasons shown (no ML claimed) |
| Sentiment analysis | Done (basic): word-list rules, overall and latest message, shown live and on the dashboard; English only |
| RAG | Basic: keyword retrieval over `knowledge.json`, honest "I don't know" |
| Appointment booking | Done: 3 free slots offered, customer picks one, saved with double-booking protection; no calendar invite yet |
| CRM integration | Basic: dashboard as mini-CRM, CSV export, optional webhook on booking (tested with a mock only) |
| Call summary | Done: template summary, LLM summary in AI mode. Business insights: average score, booking rate, top interests, sentiment split |

**End-to-end workflow demonstrated:** greeting, needs discovery, knowledge answers, scoring and sentiment, contact capture, demo booking, summary, dashboard.

## 3. Architecture
Browser (voice/text) → Flask API → extraction → keyword retrieval → rule scoring → LLM or fallback reply → SQLite → dashboard. Diagram: `WORK_CHART.md`.

## 4. Scaling to 100,000 users (design, not implemented)
- Stateless API containers behind a load balancer, autoscaled; session state moved from memory to Redis.
- SQLite replaced by managed PostgreSQL (read replica for the dashboard).
- Retrieval upgraded to embeddings in pgvector, with caching of frequent answers.
- Real-time voice via WebSocket/WebRTC with streaming STT and TTS; LLM calls queued with rate limiting and retries.
- Background workers for summaries, sentiment and CRM sync; tenant ID on every row for multi-company use.

## 5. Security
Secrets in environment variables or a secret manager; HTTPS everywhere; dashboard login with role-based access; encryption at rest; PII minimisation and retention limits; consent notice for voice recording; GDPR/DPDP-style delete on request; prompt-injection guard (the model may only state facts from retrieved knowledge); audit logs.

## 6. Deployment and cost
Container on a managed platform (e.g. cloud run/ECS) with managed Postgres and Redis, plus CI/CD. Main cost drivers are LLM tokens, STT/TTS minutes and compute. Cost per conversation ≈ (tokens × model price) + (voice minutes × STT/TTS price). Using a small model, caching and short prompts keeps this low; exact figures must be checked against current vendor pricing, and none were measured in this prototype.

## 7. Commercial feasibility
A per-seat or per-qualified-lead SaaS for SMEs (real estate, education, SaaS, services). The value is faster response and rep time saved; the cost is mostly usage-based, so margins can be protected by pricing on usage tiers.

## 8. Honest limitations
Voice and real LLM/CRM APIs were not tested; fallback mode is scripted; keyword retrieval and word-list sentiment are simple; no login; booking has no real calendar sync.
