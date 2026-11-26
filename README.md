# FortiSales AI — Autonomous AI Sales Agent

An AI sales representative that chats (text or voice) with a customer, answers product questions from a small knowledge base, qualifies the lead as **HOT / WARM / COLD**, stores the customer in SQLite and shows everything on a sales dashboard.

## Problem statement
Sales teams lose leads because nobody answers quickly, and reps waste time on low-intent visitors. A virtual sales agent can greet every visitor, collect requirements, rank buying intent and hand the sales team a ready summary.

## Objective
Demonstrate the full customer journey end to end: **greet → understand needs → answer from company knowledge → score intent → capture details → summarise → dashboard.**

## Features
| Area | What it does |
|---|---|
| AI conversation | Greets, says it is an AI, asks one follow-up at a time (product, team size, timeline, name, contact), suggests a next step |
| Voice | Mic button (browser speech recognition) + spoken replies (browser speech synthesis); text always works |
| Knowledge / RAG | `knowledge.json` (company, products, pricing, services, FAQ); simple keyword retrieval; says "I don't have that information" when nothing matches |
| Lead scoring | Transparent point rules → score 0–100 → HOT (≥60), WARM (25–59), COLD (<25), with reasons shown. **Rules only, no trained ML model** |
| Storage | One SQLite table (`fortisales.db`): name, contact, interest, requirements, score, category, reasons, summary, transcript |
| Dashboard | Total / HOT / WARM / COLD counts (click to filter), per-lead details, summary, reasons and transcript |
| Sentiment | Word-list sentiment (Positive / Neutral / Negative, handles "not good"); the agent responds more empathetically to negative messages. English only, rule-based |
| Appointment booking | Offers 3 free demo slots (weekdays), customer picks 1, 2, 3 or a weekday; saved in SQLite, double-booking blocked. No calendar invite is sent |
| CRM | Dashboard works as a mini-CRM, `Export CSV` button, and an optional webhook (`CRM_WEBHOOK_URL`) that POSTs the lead when a demo is booked |
| Business insights | Dashboard shows average score, demos booked and booking rate, top interests and sentiment split |
| Modes | AI mode with an OpenAI or Gemini key; otherwise a **clearly labelled fallback mode** (badge at the top of every page) |

## Technology stack
Python 3, Flask, SQLite, plain HTML/CSS/JavaScript, Web Speech API, `requests` (OpenAI / Gemini REST, optional).

## Repository attribution
Built **from scratch**. The build environment had no internet access, so no GitHub repository could be searched, forked or reused. No third-party code is included. The company "FortiTech Solutions" and its products, prices and statistics are **fictional sample data**.

## Project structure
```
fortisales-ai/
├── app.py              # chat, retrieval, lead scoring, SQLite, routes
├── knowledge.json      # sample company knowledge base
├── templates/
│   ├── index.html      # chat + voice + live lead panel
│   └── dashboard.html  # sales dashboard
├── static/style.css
├── requirements.txt
├── .env.example
├── README.md
├── WORK_CHART.md
└── FortiSales_AI_Report.docx         # 1-page project report
```

## Installation
```bash
cd fortisales-ai
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Environment variables (all optional)
Copy `.env.example` to `.env` and fill in **one** key. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Enables AI mode with OpenAI (`OPENAI_MODEL`, default `gpt-4o-mini`) |
| `GEMINI_API_KEY` | Enables AI mode with Gemini (`GEMINI_MODEL`, default `gemini-2.5-flash`) |
| `LLM_PROVIDER` | `openai` or `gemini`, only needed if both keys are set |
| `CRM_WEBHOOK_URL` | Optional URL (Zapier, n8n, a CRM endpoint) that receives the lead JSON when a demo is booked |
| `DB_PATH`, `PORT` | Optional database path / port (default `5000`) |

## Run
```bash
python app.py
```
Open <http://127.0.0.1:5000> (chat) and <http://127.0.0.1:5000/dashboard> (dashboard). Use Chrome or Edge for voice.

## Usage
1. Open the chat, type or tap 🎤, or use a suggestion chip.
2. Watch the **Live lead qualification** panel: gauge, category, score and reasons update every message.
3. Open the **Sales dashboard**, click a lead to see its summary, reasons and transcript.

Say *"Book a demo"* after giving your name and email to see booking.

Try: *"I need a CRM for my sales team of 25, budget approved, want a demo this week"* (HOT), *"What's the pricing? We're a team of 8, maybe next quarter"* (WARM), *"Just browsing"* (COLD), *"Do you have a mobile app?"* (honest "I don't know").

## Limitations
- Fallback mode is scripted: replies are template-based and **English only**; AI mode can reply in other languages, but scoring rules and extraction are English keywords.
- Keyword retrieval, not semantic search; unusual wording can miss a relevant answer.
- Name/contact extraction uses patterns and may miss unusual formats.
- Conversations live in server memory while running; a server restart ends open chats (saved leads stay in SQLite).
- Dashboard has no login. Booking uses fixed demo slots, not a real calendar, and sends no invite or email. The CRM webhook was only tested with a mock, not a real CRM.
- OpenAI/Gemini calls and browser voice were **not tested against real services** (see `WORK_CHART.md`).

## Future improvements
Login for the dashboard, Google/Outlook calendar sync and invites, native CRM connectors, embeddings-based retrieval, multilingual scoring, appointment booking, lead status (contacted / won / lost).
