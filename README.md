# Housekeeping Manager

Hotel owners enter rooms and bookings (form or Excel). The app creates **cleaning jobs** (on every checkout day, and every 3 days during a stay) and offers each job to a **cleaner**, nearest first. A web page shows the plan.

```bash
pip install -r requirements.txt
python reset_demo.py              # demo cleaners, a small hotel, bookings (--empty = just wipe)
uvicorn app.main:app --port 8000  # UI at http://localhost:8000
pytest                            # must stay green
```

Without credentials the app runs in **dry-run**: messages are printed in the server log, not sent.

## What exists / what is missing

| | |
|---|---|
| Web UI, bookings, Excel import, job generation, nearest-cleaner offers over SMS/Viber | done |
| **Part 1: Telegram channel** | to build |
| **Part 2: Gemini agent** | to build |

Today the cleaner is chosen by a fixed cost function (below), no AI.

## The whole flow (target design)

1. A booking creates cleaning jobs:
   - **checkout cleaning** 🧹 on the checkout day, at the checkout time;
   - **mid-stay cleaning** ✨ every `STAY_CLEAN_EVERY_DAYS` (3) nights while the guest is still in, at `STAY_CLEAN_TIME` (13:00). Example: a 7-night stay gets mid-stay cleanings after night 3 and night 6, then the checkout cleaning on day 7. A mid-stay day on Sunday moves to Monday (dropped if Monday is already the checkout).
   - No cleaning on Sundays: it moves to Monday (`SKIP_SUNDAY`), unless a new guest arrives that Sunday.
2. The app picks the nearest available cleaner and **sends her the proposed slot on Telegram** ("room 5, Tue 17/11 from 11:00, 1.2 km from you").
3. The cleaner **replies on Telegram**, in her own words ("yes", "I can't, I'm sick", "only after 14:00").
4. **Gemini reads the reply** and decides what happens next:
   - she accepts → the job is `assigned`, Gemini tells her on Telegram that it is settled;
   - she declines or can't → Gemini looks at the availability and distance of the **other cleaners**, offers the job to the best one (on Telegram), and tells the first cleaner "OK, thanks, we'll arrange it";
   - nobody can → the job turns red (`unfilled`) and the owner is alerted.
5. The owner sees all of this live on the web page, without doing anything.

Telegram is the pipe (Part 1); Gemini is the brain that reads replies and re-plans (Part 2). Today steps 2-4 work over SMS/Viber with fixed YES/NO words and no Gemini.

## Job status (what both parts need to know)

```
pending ─offer sent─► offering ─cleaner says YES─► assigned ─DONE─► completed
   ▲                     │ NO or no answer in 30 min
   └─────────────────────┘ (next cleaner is asked)      nobody left ─► unfilled
```

## How a cleaner is chosen (deterministic)

For each job, every active cleaner gets a cost; the **lowest cost is asked first**, then the next if she says no.

```
cost = d × (1 + n) + P × n
```

- `d` = distance in km from the cleaner's home to the hotel (straight line)
- `n` = upcoming jobs she already holds (accepted, or offered and not yet answered)
- `P` = load penalty, default 3 km (`LOAD_PENALTY_KM`)

What it does:

- Nobody busy (`n = 0`) → cost = distance, so the **nearest goes first**.
- Each job she takes adds `P + d` to her cost, so a near cleaner gets about +3-4 per job and the work **rotates** among the nearby cleaners instead of all going to the closest one.
- A **far** cleaner is only used when the near ones are loaded, and after one job her cost jumps by her (large) distance, so she is **not used again** soon.

Example (hotel in Plaka): Maria 0.6 km, Gianna 1.0 km, Nikos 13 km.

| | Maria | Gianna | Nikos |
|---|---|---|---|
| 0 jobs | 0.6 | 1.0 | 13 |
| 1 job | 0.6·2 + 3 = 4.2 | 1.0·2 + 3 = 5 | 13·2 + 3 = 29 |

**Hard rules** (a cleaner is skipped, whatever her cost):

- she can't take two jobs that start less than `CLEANING_MIN` (60 min) apart on the same day: nobody is in two rooms at once. **Several jobs on the same day at different times are fine** (e.g. a checkout at 11:00 and a mid-stay cleaning at 13:00);
- at most `max_jobs_per_day` jobs per day (per cleaner, in the cleaners list);
- farther than `MAX_DISTANCE_KM` (off by default);
- already declined or ignored this job.

Code: `assignment_cost`, `Agent._candidates` and `Agent._clash` in `app/agent.py`. The same rules are checked again when she answers YES. Tests: `tests/test_assignment.py`.

## Where the "who was asked / who took it" data lives (SQLite `data/agent.db`)

- `jobs`: one row per cleaning (`property_id`, `checkout_date` = the cleaning day, `checkout_time` = its start, `kind` = `checkout` | `stay`, `status`). `cleaner_id` is set **only once someone accepts**.
- `offers`: one row per message asking one cleaner about one job: `job_id`, `cleaner_id`, `status` (`pending|accepted|declined|expired|cancelled|failed`), `distance_km`, `sent_at`, `expires_at`. The full history of who was asked and what they said is here. Gemini (Part 2) can read it as context.
- `messages`: every text in and out (`direction`, `party`, `body`, `job_id`). Also `GET /messages`.
- `GET /api/state` → `jobs[]` already joins this for the UI: `cleaner` (accepted), `offered_to` / `offered_km` (asked now), `queued: true` when nobody has been asked yet and `offered_to` is just the nearest cleaner who would be asked first.

A job stays unasked (`pending`) outside offer hours (21:00-08:00) or while checkout is more than 14 days away.

## The web page (for the team and for demos)

One file, `static/index.html`, served at `/`.

- **Center: the schedule.** Rooms are rows (grouped by hotel), days are columns. The dropdown on the top left switches between **Day** view (one column per day) and **Hour** view (one column per hour of a single day). Light-blue cells are guest stays (name shown, bold edge on the check-in day); click one for details or to delete it.
- **Chips** mark each cleaning: **🧹** on the checkout day (room empties), **✨** for a mid-stay cleaning every 3 days (guest still in). Sundays move to Monday unless a guest arrives. The colour says where the job stands:

  | Chip | Meaning |
  |---|---|
  | 🟧 **Ζητήθηκε** (orange) | The job still needs an answer. Chip shows the cleaner who was asked; with ⏳ it is only queued (that cleaner will be asked next, nothing sent yet, e.g. at night). Hover for distance. |
  | 🟩 **Ανατέθηκε** (green) | A cleaner said yes. The chip shows her name. (A finished job also stays green.) |
  | 🟥 **Δεν βρέθηκε** (red) | Every cleaner declined or didn't answer. The owner must act (add a cleaner, or retry). |

- **Top right: actions.** **+ Κράτηση** (add a booking: room, check-in/out date and time), **⇪ Excel** (import bookings; rows overlapping an existing booking of the same room are skipped), **+ Καθαρίστρια** (name, phone for Telegram, address; the address is used to compute distances), **+ Ξενοδοχείο** (hotel name, address and its room numbers, e.g. `1-16`).
- **Below the schedule: cleaner cards.** Name, phone, address, channel, daily limit, and the jobs assigned to her in the coming days.
- Deleting: ✕ on a hotel row (removes its rooms and bookings), ✕ on a cleaner card, or click a booking and delete it.
- The page refreshes itself every 30 seconds. It is a plain HTML/JS page with no build step; its data comes from `/api/*` (see `app/api.py`).

Entry point for any incoming cleaner message, whatever the channel: **`Agent.handle_inbound(channel, sender, text)`**. Entry point for any outgoing message: **`Messenger.send(Recipient, body, job_id)`** in `app/messaging.py`.

Tests: use the `Env` fixture in `tests/conftest.py` (fake clock, fake messenger, no network). Never call real APIs in tests.

---

# Part 1: Telegram channel

Owner of: `app/messaging.py`, `app/main.py` (webhook), `app/db.py`, `app/config.py`.
Goal: cleaners get offers and answer YES/NO/DONE on Telegram instead of SMS.

Telegram bots cannot message a phone number: the cleaner must open the bot and press Start first, then send their phone number once so we can link the chat (same as the existing Viber flow, copy it).

**Endpoint to listen on**

| Endpoint | From | What it does |
|---|---|---|
| `POST /webhooks/telegram` | Telegram | check header `X-Telegram-Bot-Api-Secret-Token`, read `message.chat.id` + `message.text`, call `agent.handle_inbound("telegram", str(chat_id), text)` in a background task, return 200 |

Register it once with Bot API `setWebhook` (`url`, `secret_token`).

**Telegram API to call**

| Call | When |
|---|---|
| `POST https://api.telegram.org/bot<TOKEN>/sendMessage` `{chat_id, text}` | new `telegram` branch in `Messenger.send`; if the cleaner has no `telegram_id` yet, fall back to SMS |

**To change**

1. `cleaners` table: add `telegram_id TEXT` (use the `ALTER TABLE` pattern already in `db.py`); allow `channel='telegram'` (`app/loaders.py` only accepts `sms|viber`).
2. `Recipient`: add `telegram_id`.
3. `Agent._inbound`: add a `telegram` branch like `viber`: unknown chat → `_link_telegram` (match the phone number sent, save `telegram_id`, reply with the `linked` template).
4. Env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` (`config.py`, `.env.example`).
5. UI: the add-cleaner form has no channel field (`static/index.html`, `CleanerIn` in `app/api.py`). Add a channel select.
6. Tests: dry-run send, link flow, webhook secret accepted/rejected.

Do not touch `app/llm.py` (Part 2).

---

# Part 2: Gemini agent

Owner of: new `app/llm.py` + two small hooks in `app/agent.py`.
Goal: understand free-text replies ("I can't do those hours, I'm sick") and choose the best cleaner, starting from distance.

Gemini never talks to Telegram directly: every message it writes goes out through `Messenger.send`, so Part 1 delivers it. Both the "OK, it's arranged" reply to the first cleaner and the new offer to the next cleaner are sent that way.

Rules: Gemini only chooses among options our code gives it, and code validates the answer. If there is no key, the call fails or the answer is invalid, fall back to today's behaviour. The app must work without Gemini.

**External API to call**

Gemini API (`generateContent`) with JSON-schema structured output. Env: `GEMINI_API_KEY`, `GEMINI_MODEL` (don't hardcode), `LLM_ENABLED`.

**Two functions to implement in `app/llm.py`**

1. `interpret_reply(text, context) -> dict`, called from `Agent._inbound` when `parse_reply` returns `unknown`.
   - `context`: the cleaner (name, language), her open offers (job id, room, date, time), her upcoming jobs.
   - Returns `{"intent": "yes|no|done|unavailable|other", "job_id": int|null, "unavailable_from": "YYYY-MM-DD"|null, "unavailable_to": "YYYY-MM-DD"|null, "reply_text": str}`.
   - `yes` / `no` / `done` → call the existing `_answer_offer` / `_mark_done`. `unavailable` → store the dates (new table `unavailability(cleaner_id, from_date, to_date, reason)`), decline her affected offers, send `reply_text` in her language.
2. `choose_cleaner(job, candidates, context) -> int | None`, called from `Agent._offer_next` after it builds `candidates` (already filtered by the hard rules and sorted by the cost above).
   - Each candidate: id, name, km, jobs already that day, `max_jobs_per_day`.
   - `context`: the job (room, hotel, date, time, same-day turnover?) and the other jobs of that hotel that day, so one cleaner can take neighbouring rooms.
   - Returns one candidate id; anything not in `candidates` is ignored and the nearest is used.

`_offer_next` must also skip cleaners inside an `unavailability` range, with or without Gemini.

**Internal endpoints useful while building**

`GET /jobs` (all jobs with status and cleaner), `GET /messages` (message log), `POST /admin/sync` (rebuild jobs from bookings), `POST /jobs/{id}/retry` (new offer round for an unfilled job). Header `X-Admin-Token` when `ADMIN_TOKEN` is set.

**Tests:** inject a fake LLM client. Cover: invalid id → nearest cleaner; API exception → fallback; "I'm sick until Friday" → dates stored and the job goes to the next cleaner.

Do not touch `app/messaging.py` (Part 1).
