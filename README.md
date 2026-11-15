# Housekeeping Manager

* [GenAI Hackathon](https://hackathon.polymath42.com/) Team: Kerkira Dimisianou, Foteini Sfakianaki, Vaggelis Farmakis, Manousos Linardakis

* [Youtube Video Showcase](https://youtu.be/UHstZ5xnI7Y)

**Hotel cleaning that organises itself.** Enter your bookings; the app creates the cleaning jobs, offers them to the right cleaners on **Telegram**, and an **AI agent (Gemini)** reads their replies in plain Greek or English and re-plans when somebody can't come.

> "I can't do any, only room 4." · "I'm sick until Friday." · "All of them except 6." — the cleaner just writes, the schedule fixes itself.

## The problem

A small hotel or a host with several apartments plans cleanings by hand: check the bookings, text each cleaner, wait for answers, call the next one, update the sheet. A single sick cleaner means an hour of phone calls. Rigid chatbots ("reply YES 12") don't fit how people actually write.

## What it does

1. **Bookings in** — add a stay in the web page, or import an Excel file.
2. **Jobs out** — the app creates a cleaning on every checkout day, plus a mid-stay cleaning every 3 nights. No cleaning on Sundays (moved to Monday, unless a guest arrives).
3. **Smart offers** — each job is offered on Telegram to the best cleaner: close to the hotel, not overloaded, and free at that time.
4. **Natural replies** — the cleaner answers however she likes. Gemini understands it and the system acts:

   | She writes | What happens |
   |---|---|
   | `ΝΑΙ 12` / `no 7` | fast path, no AI needed |
   | "μπορώ μόνο για το 4. όχι στα υπόλοιπα" | job 4 accepted, the others declined and re-offered to the next cleaners |
   | "μπορώ για όλα εκτός από το 6" | everything accepted except job 6 |
   | "μόνο την Τρίτη μπορώ" | Gemini maps the day to the right job |
   | "είμαι άρρωστη μέχρι την Παρασκευή" | she is marked unavailable for those dates, her jobs go to other cleaners, she gets no offers meanwhile |
   | "στείλε μου τις προσφορές" / "τι δουλειές έχω;" | she gets her open offers and confirmed jobs |

5. **Re-planning** — when someone declines, Gemini looks at every other cleaner's **programme** and picks the best replacement (see below). Nobody can? The job turns red and the owner is alerted.
6. **Live board** — the owner watches all of it on the web page, with nothing to do.

## Where AI is used (and where it is not)

Gemini is the brain for **language and judgement**; plain code is the safety net.

- **Reads free text** (`interpret_reply`): turns a message into decisions per offer: accept, decline, "the rest", unavailable dates, or "show me my jobs".
- **Chooses the replacement** (`choose_cleaner`): among the cleaners that already pass every hard rule, it prefers one who works in the *same hotel* that day, avoids piling jobs on one person, and looks at gaps in her day.
- **It can only choose from what the code gives it.** Every job id and cleaner id it returns is validated; invented ones are ignored. It never talks to Telegram itself, and it cannot send or change anything.
- **The app works without Gemini.** No key, no network or a bad answer = the deterministic behaviour below (nearest cleaner, fixed `YES/NO/DONE` words).
- Gemini has **no memory** between messages. Each call gets the message plus the cleaner's current offers and schedule, nothing else.

### The programme per cleaner, as Excel

After every change the app rewrites **`data/cleaner_schedules.xlsx`** from the database: sheets `Cleaners`, `Schedule` (one row per job a cleaner holds or was offered, with start/end/km), `Open jobs`, and one sheet per cleaner. The database stays the source of truth; the workbook is read back and given to Gemini as context, and the owner can open it in Excel.

## How a cleaner is picked (deterministic baseline)

For each job every active cleaner gets a cost, and the lowest is asked first:

```
cost = d × (1 + n) + P × n
```

`d` = km from her home to the hotel, `n` = jobs she already holds (accepted or offered), `P` = load penalty (3 km). Nobody busy → the nearest goes first; every job adds `P + d`, so work **rotates** among nearby cleaners and a far cleaner is not used twice in a row.

**Hard rules** (skipped whatever the cost): two jobs less than 60 min apart, more than `max_jobs_per_day`, already declined this job, away on that date (`unavailability`), or no way to reach her (e.g. not yet linked on Telegram). Gemini picks only among cleaners who pass these.

Code: `assignment_cost`, `Agent._candidates`, `Agent._clash`, `Agent._llm_pick` in `app/agent.py`.

## Onboarding a cleaner in one tap

Telegram bots can't message a phone number, so each cleaner must press **Start** once. To make this painless:

- Each cleaner card has **🔗 Telegram**: a personal one-time link `t.me/<bot>?start=<code>`, with QR code, copy and share buttons.
- Or **✉ send by email**: she gets a Greek/English email with a big "Open in Telegram" button (Gmail SMTP). New cleaners with an email get it automatically.
- She taps, presses Start, and she is linked, and her waiting offers arrive immediately. The code works once, so a forwarded link is useless.

## Architecture

```
 Web page (static/index.html)            Owner: bookings, Excel import, cleaners, live board
        │  /api/*
        ▼
 FastAPI ── Agent (app/agent.py) ── SQLite (data/agent.db)
              │      │   ▲
              │      │   └── cleaner_schedules.xlsx (rebuilt from the DB, read by Gemini)
              │      └────── Gemini (app/llm.py): read replies, choose replacement
              ▼
        Messenger (app/messaging.py) ──► Telegram Bot API
              ▲
 Telegram ────┘  long polling (no public URL) or POST /webhooks/telegram (secret token)
```

| File | Role |
|---|---|
| `app/agent.py` | the scheduling brain: jobs, offers, replies, re-planning |
| `app/llm.py` | Gemini client (structured JSON output) |
| `app/telegram.py` | receive messages (polling / webhook), bot username |
| `app/messaging.py` | send on Telegram; dry-run mode |
| `app/schedule_xlsx.py` | per-cleaner programme as Excel |
| `app/mailer.py` | email (Gmail SMTP) for the one-tap link |
| `app/api.py`, `static/index.html` | web UI and its API |

Job status flow:

```
pending ─offer sent─► offering ─cleaner accepts─► assigned ─DONE─► completed
   ▲                     │ declines / no answer in 30 min
   └─────────────────────┘ (next cleaner is asked)      nobody left ─► unfilled
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in the keys below (all optional: without them it runs in dry-run)
python reset_demo.py              # demo: 5 cleaners, a 3-room hotel, bookings for the next 10 days
uvicorn app.main:app --env-file .env --port 8100   # UI at http://localhost:8100
pytest                            # 73 tests, no network
```

`reset_demo.py` keeps cleaners' Telegram links, so you don't have to relink after a reset. Start the server first if you want it to create the jobs immediately.

| `.env` | What for |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from @BotFather (`/newbot`). Without a webhook secret the app long-polls, so no public URL is needed |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Google AI Studio key. Empty = no AI, fixed `YES/NO` words |
| `OWNER_TELEGRAM_CHAT_ID` | optional: the owner's Telegram chat id (message the bot, then look at `GET /messages`). Gets alerts: job unfilled, cancelled, done |
| `ADMIN_TOKEN` | password the web page asks for once (required when real messaging is on) |
| `SMTP_USER`, `SMTP_PASSWORD` | Gmail address + an **App Password** (Google Account → Security → 2-Step Verification → App passwords) for the link emails |
| `TELEGRAM_WEBHOOK_SECRET`, `PUBLIC_BASE_URL` | optional: use the webhook instead of polling |

Without credentials the app runs in **dry-run**: messages are printed in the server log, nothing is sent. Offers go out between 08:00 and 21:00 (Athens) only.

## Demo script (3 minutes)

1. Open the board: rooms × days, 🧹 checkout and ✨ mid-stay cleanings, orange chips = waiting for an answer.
2. **🔗 Telegram** on a cleaner → send the link (email or QR) → she taps Start → her offers arrive.
3. Reply "ναι 4". The chip turns green, the owner sees it live.
4. Reply "μπορώ μόνο για το 5. όχι στα υπόλοιπα": one message, five decisions, and the declined jobs are offered to the next cleaners.
5. Reply "είμαι άρρωστη μέχρι την Παρασκευή": her jobs move to other cleaners, and she is skipped meanwhile.
6. Ask "τι δουλειές έχω;": the bot lists her jobs.
7. Open `data/cleaner_schedules.xlsx`: the programme per cleaner that Gemini reasons over.

## Data (SQLite `data/agent.db`)

`jobs` (one cleaning each, `cleaner_id` set only when someone accepts) · `offers` (who was asked, what they said, distance) · `messages` (every text in and out) · `unavailability` (dates a cleaner is away) · `cleaners` (incl. `telegram_id`, `email`) · `properties`, `bookings`. `GET /api/state` joins this for the page.

## Limits and next steps

- One owner, one SQLite file, one server process: enough for a hotel, not a platform.
- Distances are straight-line (haversine), not travel time.
- Offers are sent to one cleaner at a time (30 min each) to keep the work fair; a "send to several at once" mode would be faster.
- "Only after 14:00" is treated as a decline of that job, not as a time constraint to renegotiate.
- `/job/{id}` is a prototype page with fixed text; next step: fill it from the database and link it from the Telegram offer.
- Telegram is the only channel (no SMS / Viber), so a cleaner must press Start in the bot once.
- The web UI is in Greek; the cleaners are called the **Ομάδα Καθαρισμού** (cleaning team).

## Tests

`pytest` runs 73 tests with a fake clock, fake messenger and fake Gemini: the offer rotation, the Telegram link flow, webhook secrets, every Gemini case above (invalid ids, API errors, batch answers, "all except 6") and the fallbacks. Never call real APIs in tests.
