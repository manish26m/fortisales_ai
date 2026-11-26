"""FortiSales AI - an AI sales agent that chats, qualifies leads and fills a dashboard.

One file on purpose, so it is easy to read and explain:
  1. Config + knowledge base      4. Lead scoring (rules, explainable)
  2. Retrieval (keyword match)    5. Replies (LLM if key exists, else fallback)
  3. Slot extraction (name etc.)  6. SQLite storage + Flask routes
"""
import csv
import io
import json
import os
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request

BASE = Path(__file__).parent


# ---------------------------------------------------------------- 1. config
def load_env():
    """Tiny .env reader so we do not need an extra dependency."""
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()
DB_PATH = os.environ.get("DB_PATH", str(BASE / "fortisales.db"))
KB = json.loads((BASE / "knowledge.json").read_text(encoding="utf-8"))
app = Flask(__name__)


def llm_provider():
    """Return 'openai', 'gemini' or None (None = rule-based fallback mode)."""
    wanted = os.environ.get("LLM_PROVIDER", "").lower()
    has_openai, has_gemini = bool(os.environ.get("OPENAI_API_KEY")), bool(os.environ.get("GEMINI_API_KEY"))
    if wanted == "openai" and has_openai:
        return "openai"
    if wanted == "gemini" and has_gemini:
        return "gemini"
    if has_openai:
        return "openai"
    if has_gemini:
        return "gemini"
    return None


# ------------------------------------------------------------ 2. retrieval
STOP = set(
    "a an the is are was were be do does did can could would should will i you we it my our your me to of for in "
    "on at and or but with about what how which who when where this that there any have has get got please tell "
    "like want need im am so if as by from not no yes".split()
)


def stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text):
    return {stem(w) for w in re.findall(r"[a-z0-9$]+", text.lower()) if w not in STOP and len(w) > 1}


for chunk in KB["chunks"]:  # precompute once
    chunk["_kw"] = tokens(" ".join(chunk["keywords"]))
    chunk["_tx"] = tokens(chunk["title"] + " " + chunk["text"])


def retrieve(query, k=2, min_score=2):
    """Keyword retrieval: +2 per query word in a chunk's keywords, +1 per word in its text."""
    query_tokens = tokens(query)
    scored = []
    for chunk in KB["chunks"]:
        score = sum(2 if t in chunk["_kw"] else 1 if t in chunk["_tx"] else 0 for t in query_tokens)
        if score >= min_score:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: -pair[0])
    return [chunk for _, chunk in scored[:k]]


def chunk_by_id(chunk_id):
    return next(c for c in KB["chunks"] if c["id"] == chunk_id)


# ------------------------------------------------------ 3. slot extraction
PRODUCTS = {
    "FortiCRM": r"\b(crm|forticrm|sales pipeline|pipeline|leads?|contacts?|deals?|follow-?ups?)\b",
    "FortiDesk": r"\b(fortidesk|help ?desk|support tickets?|tickets?|customer support|ticketing|live chat)\b",
    "FortiInsights": r"\b(fortiinsights|analytics|reports?|reporting|dashboards?|insights|forecast\w*)\b",
    "Onboarding & Training": r"\b(onboarding|migration|migrate|training|implementation)\b",
}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)\+?\d[\d\s().-]{7,}\d")
TEAM = re.compile(
    r"\b(\d{1,5})\s*(?:\+\s*)?(?:users?|people|employees|reps|agents|seats|members|sales ?reps?|staff)\b"
    r"|\bteam of (\d{1,5})\b|\b(\d{1,5})-person\b",
    re.I,
)
TIMELINE = re.compile(
    r"\b(today|tomorrow|this week|next week|this month|next month|asap|urgent(?:ly)?|immediately|right away|"
    r"next quarter|next year|(?:in )?(?:a few|\d+) (?:days|weeks|months)|by end of \w+)\b",
    re.I,
)
NAME_STRONG = re.compile(r"(?i:my name is|name's|name is|call me)\s+([A-Za-z][A-Za-z'-]*)(?:\s+([A-Z][A-Za-z'-]*))?")
NAME_IM = re.compile(r"\b(?:I'm|I am|Im)\s+([A-Z][a-z'-]+)(?:\s+([A-Z][a-z'-]+))?")
NOT_NAMES = {"Looking", "Interested", "Not", "Just", "Trying", "Thinking", "Here", "Wondering", "Curious", "Also", "And"}
NEED_SENTENCE = re.compile(
    r"\b(need|looking for|want|require|trying to|interested in|team|users|employees|integrat\w*|track|manage|"
    r"automat\w*|report\w*|tickets?|support|pipeline|budget|replace|switch)\b",
    re.I,
)
BUY_SIGNAL = re.compile(r"\b(demo|trial|buy|purchase|sign ?up|quote|proposal|get started|ready to|book|schedule|appointment|meeting)\b", re.I)
NOT_INTERESTED = re.compile(
    r"\b(not interested|no interest|don'?t need|do not need|no need|not looking|unsubscribe|not now|"
    r"just (?:browsing|looking|curious))\b",
    re.I,
)

BOOK_INTENT = re.compile(r"\b(book|schedule|appointment|meeting|demo|call me|set up a)\b", re.I)

# Sentiment: a small word list (rules, not ML). English only.
POSITIVE = set("good great awesome love excellent perfect happy thanks thank helpful amazing nice impressive fantastic excited wonderful".split())
NEGATIVE = set("bad terrible awful hate angry frustrated frustrating annoyed annoying slow expensive useless worst disappointed disappointing confusing unhappy waste rude broken".split())


def sentiment_score(text):
    words = re.findall(r"[a-z']+", text.lower())
    total = 0
    for i, word in enumerate(words):
        value = 1 if word in POSITIVE else -1 if word in NEGATIVE else 0
        if value and i and words[i - 1] in {"not", "no", "never", "isn't", "wasn't", "don't"}:
            value = -value  # "not good" is negative
        total += value
    return total


def sentiment_label(score):
    return "Positive" if score > 0 else "Negative" if score < 0 else "Neutral"


def sentiment_overall(state):
    return sentiment_label(sum(sentiment_score(m["content"]) for m in state["messages"] if m["role"] == "user"))


def new_state():
    return {"messages": [], "name": None, "contact": None, "interest": [], "team": None, "timeline": None,
            "asked": {}, "closed": False, "awaiting": None, "offered": [], "booked": None,
            "sentiment_now": "Neutral"}


def extract(state, text):
    """Pull name / contact / interest / team size / timeline out of what the customer typed."""
    email, phone = EMAIL.search(text), PHONE.search(text)
    if email:
        state["contact"] = email.group(0)
    elif phone and 8 <= len(re.sub(r"\D", "", phone.group(0))) <= 15:
        state["contact"] = phone.group(0).strip()

    for pattern in (NAME_STRONG, NAME_IM):
        match = pattern.search(text)
        if match and match.group(1).capitalize() not in NOT_NAMES:
            second = match.group(2) if match.group(2) and match.group(2) not in NOT_NAMES else ""
            state["name"] = f"{match.group(1).capitalize()} {second}".strip()
            break
    else:  # we just asked "what's your name?" and they answered with 1-3 plain words
        if state["awaiting"] == "name" and not state["name"]:
            bare = EMAIL.sub("", PHONE.sub("", text)).strip(" .,!")
            words = bare.split()
            if 1 <= len(words) <= 3 and all(re.fullmatch(r"[A-Za-z'-]+", w) for w in words) \
                    and words[0].lower() not in {"no", "yes", "skip", "later", "nope", "none", "not"}:
                state["name"] = " ".join(w.capitalize() for w in words)

    for product, pattern in PRODUCTS.items():
        if re.search(pattern, text, re.I) and product not in state["interest"]:
            state["interest"].append(product)

    team = TEAM.search(text)
    if team:
        state["team"] = next(g for g in team.groups() if g)
    timeline = TIMELINE.search(text)
    if timeline:
        state["timeline"] = timeline.group(0).lower()


def user_text(state):
    return " ".join(m["content"] for m in state["messages"] if m["role"] == "user")


def requirements_of(state):
    """Requirements = the customer's own sentences that describe a need."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", user_text(state)):
        sentence = sentence.strip()
        if sentence and NEED_SENTENCE.search(sentence) and sentence not in found and "?" not in sentence:
            found.append(sentence)
    return " ".join(found)[:400]


# ------------------------------------------------------- 4. lead scoring
# (label, regex-or-function, points). Every rule fires at most once. Plain rules, no ML.
RULES = [
    ("Asked to buy, sign up or get a quote", r"\b(buy|purchase|sign ?up|get started|quote|proposal|subscribe|go ahead|ready to)\b", 30),
    ("Asked for a demo or trial", r"\b(demo|trial|try it|pilot)\b", 20),
    ("Stated a clear need", r"\b(need|looking for|want|require|must have|trying to|replace|switch from)\b|(?<!not )interested in", 15),
    ("Product interest identified", lambda s: bool(s["interest"]), 10),
    ("Near-term timeline", r"\b(today|tomorrow|this week|next week|this month|next month|asap|urgent|immediately|right away|by end of)\b", 20),
    ("Longer-term timeline", r"\b(next quarter|next year|later this year|few months|eventually)\b", 5),
    ("Budget mentioned", r"\b(budget|approved|funding|allocated)\b|\$\s?\d", 15),
    ("Team size given", lambda s: bool(s["team"]), 10),
    ("Asked about pricing or plans", r"\b(price|pricing|cost|how much|plans?)\b", 10),
    ("Comparing options", r"\b(compare|comparison|alternatives?|evaluating|researching|versus)\b", 5),
    ("Shared contact details", lambda s: bool(s["contact"]), 5),
    ("Said not interested / just browsing", r"\b(not interested|no interest|don'?t need|do not need|no need|not looking|not now|just (?:browsing|looking|curious))\b", -40),
    ("Budget is a blocker", r"\b(no budget|can'?t afford|too expensive|out of budget)\b", -25),
    ("Wants to postpone", r"\b(maybe later|some other time|not right now|in the future|no rush)\b", -15),
]


def score_lead(state):
    text = user_text(state).lower()
    score, reasons = 0, []
    for label, test, points in RULES:
        hit = test(state) if callable(test) else re.search(test, text)
        if hit:
            score += points
            reasons.append(f"{points:+d}  {label}")
    score = max(0, min(100, score))
    category = "HOT" if score >= 60 else "WARM" if score >= 25 else "COLD"
    return score, category, reasons or ["Not enough information yet"]


# ------------------------------------------------------------ 5. replies
def is_question(text):
    return "?" in text or bool(re.match(r"\s*(what|how|do|does|can|is|are|which|who|when|where|why|tell me)\b", text, re.I))


def missing(state, slot):
    return not (state["interest"] if slot == "interest" else state[slot])


def next_slot(state, text):
    order = ["interest", "team", "timeline", "name", "contact"]
    if BUY_SIGNAL.search(text):  # ready to buy -> get their details first
        order = ["name", "contact", "interest", "team", "timeline"]
    for slot in order:
        if missing(state, slot) and state["asked"].get(slot, 0) < 2:
            return slot
    return None


def question_for(slot, state):
    product = state["interest"][0] if state["interest"] else "it"
    greeting = f"Thanks, {state['name'].split()[0]}! " if state["name"] else ""
    return {
        "interest": "What are you hoping to solve: managing sales leads, handling support tickets, or getting clearer reports?",
        "team": f"Roughly how many people would use {product}?",
        "timeline": "When would you like to have something up and running?",
        "name": "May I ask your name?",
        "contact": greeting + "What's the best email or phone number for our sales team to reach you?",
    }[slot]


CLOSINGS = {
    "HOT": "This looks like a strong fit, so I've flagged you as a priority lead for our sales team to follow up at {c} and set up a demo.",
    "WARM": "I've recorded your details so our sales team can follow up at {c} with a tailored comparison. You can also start the 14-day free trial any time.",
    "COLD": "I've saved your details, no pressure. Our team can send a short overview to {c}, and you're welcome to come back whenever you're ready.",
}
UNKNOWN = "I don't have that information in my product knowledge, and I'd rather not guess. I can note the question for a sales specialist to confirm."


PRODUCT_CHUNK = {"FortiCRM": "forticrm", "FortiDesk": "fortidesk", "FortiInsights": "fortiinsights",
                 "Onboarding & Training": "services"}


def fallback_reply(state, text, hits, new_interest, slot, category):
    if NOT_INTERESTED.search(text):
        return "No problem at all, thanks for letting me know. If anything changes I'm happy to help, and you can ask me anything about our products first."
    empathy = "I'm sorry about that, let me help." if state["sentiment_now"] == "Negative" else ""
    parts = []
    if is_question(text):
        parts.append(hits[0]["text"] if hits else UNKNOWN)
    elif new_interest:  # first time they mention a product: recommend it from the knowledge base
        parts.append(chunk_by_id(PRODUCT_CHUNK[new_interest[0]])["text"])
    elif re.fullmatch(r"\s*(hi|hello|hey)\W*", text, re.I):
        parts.append("Hello! I'm Forti, FortiTech's AI sales representative.")
    if slot:
        parts.append(question_for(slot, state))
    elif not state["closed"]:
        parts.append(CLOSINGS[category].format(c=state["contact"] or "your preferred contact"))
    elif not parts:
        parts.append("Is there anything else I can help you with?")
    return " ".join(([empathy] if empathy else []) + parts)


def system_prompt(state, hits, slot, category):
    known = {k: state[k] for k in ("name", "contact", "team", "timeline")}
    known["interest"] = ", ".join(state["interest"])
    if slot:
        goal = f"Naturally ask for ONE missing detail at the end of your reply: {question_for(slot, state)}"
    elif not state["closed"]:
        goal = (f"You have everything you need. Wrap up: say the details are recorded for the sales team to follow up "
                f"(lead looks {category}), and suggest a next step (demo or 14-day trial). Do not promise anything else.")
    else:
        goal = "Answer the question, then ask if there is anything else."
    context = "\n".join(f"- {c['title']}: {c['text']}" for c in hits) or "(nothing relevant found)"
    return (
        "You are Forti, an AI sales representative for FortiTech Solutions. Always say you are an AI if asked.\n"
        f"Company: {KB['company']}\n\nKNOWLEDGE (the only facts you may state about products, prices and policies):\n{context}\n\n"
        "Rules: be warm, concise (max 70 words) and conversational. If the customer asks something that is not in "
        "KNOWLEDGE, say you don't have that information and offer to have a specialist confirm; never invent prices, "
        "features or promises. Reply in the same language the customer uses. Do not claim emails were sent.\n"
        f"Customer sentiment right now: {state['sentiment_now']} (if Negative, acknowledge it briefly and empathetically).\n"
        f"Known about customer: {json.dumps(known)}\n{goal}"
    )


def call_llm(provider, system, messages, max_tokens=300):
    messages = messages[-12:]
    if provider == "openai":
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system}] + messages},
            timeout=25,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in messages]
    while contents and contents[0]["role"] != "user":  # Gemini must start with a user turn
        contents.pop(0)
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
              "generationConfig": {"maxOutputTokens": max_tokens}},
        timeout=25,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def template_summary(state, score, category, reasons):
    who = state["name"] or "An unnamed visitor"
    bits = [f"{who} ({state['contact'] or 'no contact yet'}) is interested in {', '.join(state['interest']) or 'an unspecified product'}."]
    bits.append(f"Requirements: {requirements_of(state) or 'not stated yet'}")
    if state["team"]:
        bits.append(f"Team size: {state['team']}.")
    if state["timeline"]:
        bits.append(f"Timeline: {state['timeline']}.")
    bits.append(f"Sentiment: {sentiment_overall(state)}.")
    if state["booked"]:
        bits.append(f"Demo booked for {slot_label(state['booked'])}.")
    bits.append(f"Classified {category} ({score}/100); strongest signals: {'; '.join(r.split('  ', 1)[-1] for r in reasons[:3])}.")
    return " ".join(bits)


# ------------------------------------------------- 6. storage + routes
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS leads (
                session_id TEXT PRIMARY KEY, name TEXT, contact TEXT, interest TEXT, requirements TEXT,
                score INTEGER, category TEXT, reasons TEXT, summary TEXT, transcript TEXT,
                created_at TEXT, updated_at TEXT, sentiment TEXT, appointment TEXT)"""
        )
        have = {row[1] for row in conn.execute("PRAGMA table_info(leads)")}
        for col in ("sentiment", "appointment"):  # upgrade a database made by the first version
            if col not in have:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, name TEXT, contact TEXT,
                slot TEXT UNIQUE, created_at TEXT)"""
        )


def save_lead(sid, state, score, category, reasons, summary):
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        conn.execute(
            """INSERT INTO leads (session_id,name,contact,interest,requirements,score,category,reasons,summary,
                                  transcript,created_at,updated_at,sentiment,appointment)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET name=excluded.name, contact=excluded.contact,
                 interest=excluded.interest, requirements=excluded.requirements, score=excluded.score,
                 category=excluded.category, reasons=excluded.reasons, summary=excluded.summary,
                 transcript=excluded.transcript, updated_at=excluded.updated_at,
                 sentiment=excluded.sentiment, appointment=excluded.appointment""",
            (sid, state["name"], state["contact"], ", ".join(state["interest"]), requirements_of(state), score,
             category, json.dumps(reasons), summary, json.dumps(state["messages"]), now, now,
             sentiment_overall(state), state["booked"]),
        )


# ---- appointment booking: offer 3 free slots, customer picks one, saved to SQLite ----
def slot_label(slot):
    if not slot:
        return ""
    when = datetime.strptime(slot, "%Y-%m-%d %H:%M")
    return f"{when:%a %d %b} at {when:%H:%M}"


app.jinja_env.filters["slot"] = slot_label


def free_slots(count=3):
    with db() as conn:
        taken = {row[0] for row in conn.execute("SELECT slot FROM appointments")}
    out, day, times, i = [], datetime.now().date(), ["10:00", "14:00", "11:00"], 0
    while len(out) < count:
        day += timedelta(days=1)
        if day.weekday() >= 5:  # skip Saturday and Sunday
            continue
        slot = f"{day.isoformat()} {times[i % 3]}"
        i += 1
        if slot not in taken:
            out.append(slot)
    return out


def pick_slot(offered, text):
    lowered = text.lower()
    digit = re.search(r"\b([1-3])\b", lowered)
    if digit and int(digit.group(1)) <= len(offered):
        return offered[int(digit.group(1)) - 1]
    for i, word in enumerate(("first", "second", "third")):
        if word in lowered and i < len(offered):
            return offered[i]
    for slot in offered:
        if datetime.strptime(slot, "%Y-%m-%d %H:%M").strftime("%A").lower() in lowered:
            return slot
    return None


def offer_text(state):
    first = state["name"].split()[0] if state["name"] else "there"
    options = "\n".join(f"{i}) {slot_label(s)}" for i, s in enumerate(state["offered"], 1))
    return (f"Great, {first}! Let's set up a 30-minute demo with a sales specialist. "
            f"Here are the next free slots:\n{options}\nReply with 1, 2 or 3.")


def booking_turn(state, sid, text, slot, category):
    """Return a reply if this turn is about booking a demo, otherwise None."""
    if state["booked"]:
        if BOOK_INTENT.search(text):
            return f"You're already booked for {slot_label(state['booked'])}. Anything else I can help with?"
        return None
    if state["offered"]:
        choice = pick_slot(state["offered"], text)
        if choice:
            try:
                with db() as conn:
                    conn.execute("INSERT INTO appointments (session_id,name,contact,slot,created_at) VALUES (?,?,?,?,?)",
                                 (sid, state["name"], state["contact"], choice, datetime.now().isoformat(timespec="seconds")))
            except sqlite3.IntegrityError:  # someone else took it a moment ago
                state["offered"] = free_slots()
                return "Sorry, that slot was just taken. " + offer_text(state)
            state["booked"], state["just_booked"] = choice, True
            return (f"You're booked for {slot_label(choice)}. A sales specialist will meet you then at "
                    f"{state['contact']}. (This prototype does not send a calendar invite yet.)")
    wants = BOOK_INTENT.search(text) or (slot is None and not state["closed"] and category != "COLD")
    if wants and state["name"] and state["contact"]:
        state["offered"] = free_slots()
        state["closed"] = True
        return offer_text(state)
    return None


def push_to_crm(payload):
    """Optional: POST the lead to any CRM/Zapier/n8n webhook set in CRM_WEBHOOK_URL."""
    url = os.environ.get("CRM_WEBHOOK_URL")
    if not url:
        return
    try:
        requests.post(url, json=payload, timeout=8).raise_for_status()
    except Exception as exc:
        app.logger.warning("CRM webhook failed: %s", exc)


SESSIONS = {}
init_db()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    provider = llm_provider()
    return jsonify(mode=f"AI mode ({provider})" if provider else "Fallback mode (no API key)", llm=bool(provider))


@app.post("/api/chat")
def chat():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("message") or "").strip().replace("\u2019", "'")[:1000]
    if not text:
        return jsonify(error="Please type or say something."), 400
    sid = data.get("session_id") or str(uuid.uuid4())
    state = SESSIONS.setdefault(sid, new_state())

    known_before = list(state["interest"])
    state["messages"].append({"role": "user", "content": text})
    extract(state, text)
    state["sentiment_now"] = sentiment_label(sentiment_score(text))
    new_interest = [p for p in state["interest"] if p not in known_before]
    score, category, reasons = score_lead(state)
    hits = retrieve(text)
    not_interested = bool(NOT_INTERESTED.search(text))
    slot = None if not_interested else next_slot(state, text)

    provider = llm_provider()
    mode = f"AI mode ({provider})" if provider else "Fallback mode (no API key)"
    reply = None if not_interested else booking_turn(state, sid, text, slot, category)  # booking is deterministic
    if reply is None and provider and not not_interested:
        try:
            reply = call_llm(provider, system_prompt(state, hits, slot, category), state["messages"])
        except Exception as exc:  # network error, bad key, quota... keep the demo alive
            app.logger.warning("LLM call failed, using fallback: %s", exc)
            mode = "Fallback mode (AI request failed)"
    if reply is None:
        reply = fallback_reply(state, text, hits, new_interest, slot, category)

    if slot:
        state["asked"][slot] = state["asked"].get(slot, 0) + 1
    state["awaiting"] = slot
    if slot is None and not not_interested:
        state["closed"] = True
    state["messages"].append({"role": "assistant", "content": reply})

    summary = template_summary(state, score, category, reasons)
    if provider and mode.startswith("AI"):
        try:
            transcript = "\n".join(f"{m['role']}: {m['content']}" for m in state["messages"])
            summary = call_llm(provider, "Summarise this sales conversation in 2-3 sentences for a sales rep: "
                               "customer need, product interest, team size, timeline, sentiment, any booked demo, "
                               "and suggested next step. Use only facts from the conversation.",
                               [{"role": "user", "content": transcript}], max_tokens=200)
        except Exception as exc:
            app.logger.warning("LLM summary failed, using template: %s", exc)
    save_lead(sid, state, score, category, reasons, summary)

    lead = {"name": state["name"], "contact": state["contact"], "interest": state["interest"],
            "requirements": requirements_of(state), "score": score, "category": category, "reasons": reasons,
            "summary": summary, "sentiment": sentiment_overall(state), "sentiment_now": state["sentiment_now"],
            "appointment": slot_label(state["booked"])}
    if state.pop("just_booked", False):
        push_to_crm({**lead, "appointment_slot": state["booked"], "session_id": sid})
    return jsonify(reply=reply, mode=mode, session_id=sid, lead=lead)


@app.get("/dashboard")
def dashboard():
    wanted = request.args.get("category", "").upper()
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY updated_at DESC")]
        appointments = [dict(r) for r in conn.execute("SELECT * FROM appointments ORDER BY slot")]
    counts = {c: sum(1 for r in rows if r["category"] == c) for c in ("HOT", "WARM", "COLD")}
    for row in rows:
        row["reasons"] = json.loads(row["reasons"] or "[]")
        row["transcript"] = json.loads(row["transcript"] or "[]")
    interests = Counter(i for r in rows for i in (r["interest"] or "").split(", ") if i)
    insights = {
        "avg_score": round(sum(r["score"] for r in rows) / len(rows)) if rows else 0,
        "booked": len(appointments),
        "booking_rate": round(100 * len(appointments) / len(rows)) if rows else 0,
        "top_interest": interests.most_common(3),
        "sentiment": Counter(r["sentiment"] or "Neutral" for r in rows),
    }
    shown = [r for r in rows if r["category"] == wanted] if wanted in counts else rows
    return render_template("dashboard.html", leads=shown, total=len(rows), counts=counts, active=wanted,
                           mode=status().json["mode"], insights=insights, appointments=appointments)


def csv_safe(value):
    """Stop spreadsheet formula injection when the export is opened in Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@app.get("/export.csv")
def export_csv():
    with db() as conn:
        rows = conn.execute("SELECT name,contact,interest,requirements,score,category,sentiment,appointment,"
                            "summary,updated_at FROM leads ORDER BY updated_at DESC").fetchall()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "contact", "interest", "requirements", "score", "category", "sentiment",
                     "appointment", "summary", "updated_at"])
    for row in rows:
        writer.writerow([csv_safe(v) for v in row])
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=fortisales_leads.csv"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
