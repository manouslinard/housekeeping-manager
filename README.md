# Housekeeping Manager

* [GenAI Hackathon](https://hackathon.polymath42.com/) Team: Kerkyra Dimisianou, Foteini Sfakianaki, Vaggelis Farmakis, Manousos Linardakis

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
   | "μπορώ μόνο για το 4. όχι στα υπόλοιπα" | job 4 accepted, the others declined and re-offered to the next cleaners |
   | "μπορώ για όλα εκτός από το 6" | everything accepted except job 6 |
   | "μόνο την Τρίτη μπορώ" | Gemini maps the day to the right job |
   | "είμαι άρρωστη μέχρι την Παρασκευή" | she is marked unavailable for those dates, her jobs go to other cleaners, she gets no offers meanwhile |
   | "στείλε μου τις προσφορές" | she gets her open offers assigned to her |

5. **Re-planning** — when someone declines, Gemini looks at every other cleaner's **programme** and picks the best replacement (see below). Nobody can? The job turns red and the owner is alerted via the live dashboard.
6. **Live board** — the owner watches all of it on the web page.

## Where AI is used (and where it is not)

Gemini is the brain for **language and judgement**; plain code is the safety net.

- **Reads free text** (`interpret_reply`): turns a message into decisions per offer: accept, decline, "the rest", unavailable dates, or "show me my jobs".
- **Chooses the replacement** (`choose_cleaner`): among the cleaners that already pass every hard rule, it prefers one who works in the *same hotel* that day, avoids piling jobs on one person, and looks at gaps in her day (see cost function below).

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
- Or **✉ send by email**: she gets a Greek/English email with a big "Open in Telegram" button (Gmail SMTP).
- She taps, presses Start, and she is linked, and her waiting offers arrive immediately.

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
   ▲                     │ declines
   └─────────────────────┘ (next cleaner is asked)      nobody left ─► unfilled
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in the keys below (all optional: without them it runs in dry-run)
python reset_demo.py              # demo: 5 cleaners, a 3-room hotel, bookings for the next 10 days
uvicorn app.main:app --env-file .env --port 8100   # UI at http://localhost:8100
```

For running tests (after starting uvicorn command):
```bash
pytest                            # 73 tests, no network
```

| `.env` | What for |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from @BotFather (`/newbot`). Used for sending Telegram Messages |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Google AI Studio key for Gemini Access |
| `ADMIN_TOKEN` | password the web page asks for once for the hotel owner (admin) |
| `SMTP_USER`, `SMTP_PASSWORD` | Gmail address + an App Password  for the link emails |

Without credentials the app runs in **dry-run**: messages are printed in the server log, nothing is sent.

## Short function description

1. Open the board: rooms × days, 🧹 checkout and ✨ mid-stay cleanings, orange chips = waiting for an answer.
2. **🔗 Telegram** on a cleaner → send the link (email or QR) → she taps Start → her offers arrive.
3. Reply "ναι x" where x is the offer number sent in her Telegram. The chip turns green, the owner sees it live.
4. Reply "μπορώ μόνο για το y. όχι στα υπόλοιπα": one message declines many jobs, and accepts one for quick communication Declined jobs are offered to the next cleaners.
5. Reply "είμαι άρρωστη μέχρι την Παρασκευή": her jobs move to other cleaners, and she is skipped meanwhile.
6. Ask "τι δουλειές έχω;": the bot lists her jobs.

## Limits and next steps

- One owner, one SQLite file: not production ready yet.
- Telegram is the only channel (no SMS / Viber yet), so a cleaner should install this.
- Assumes a standard hour of service. Longer cleanings (example full room, or just towels etc), require more or less time. Also can incorporate job urgency (for example urgent cleaning).
