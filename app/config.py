"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _s(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _i(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def _f(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    db_path: str = "data/agent.db"
    cleaners_csv: str = "data/cleaners.csv"
    properties_csv: str = "data/properties.csv"
    timezone: str = "Europe/Athens"
    default_country_code: str = "+30"

    sync_interval_min: int = 15      # how often calendars are polled
    offer_timeout_min: int = 30      # how long a cleaner has to answer before the next one is asked
    offer_lead_days: int = 14        # start asking cleaners this many days before checkout
    horizon_days: int = 90           # ignore checkouts further away than this
    quiet_start_hour: int = 21       # no texts from this hour...
    quiet_end_hour: int = 8          # ...until this hour (local time)
    reminder_hour: int = 18          # day-before reminder is sent from this hour
    max_distance_km: float = 0.0     # 0 = no limit
    default_max_jobs_per_day: int = 3
    stay_clean_every_days: int = 3   # also clean during a stay every N nights (0 = only at checkout)
    stay_clean_time: str = "13:00"   # time of those mid-stay cleanings (after checkouts, before check-ins)
    cleaning_min: int = 60           # a cleaner can't start two jobs less than this apart
    load_penalty_km: float = 3.0     # extra cost per job a cleaner already holds (see assignment_cost)
    default_checkout_time: str = "11:00"
    skip_sunday: bool = True         # Sunday checkouts are cleaned on Monday (unless a guest arrives that Sunday)
    ignore_airbnb_blocks: bool = True  # "Airbnb (Not available)" = owner block, not a guest

    owner_phone: str = ""            # gets alerts (unfilled / cancelled / done)
    owner_channel: str = "sms"
    owner_language: str = "en"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""
    viber_auth_token: str = ""
    viber_sender_name: str = "Cleaning"

    public_base_url: str = ""        # e.g. https://cleaning.example.com (used to verify Twilio signatures)
    verify_webhooks: bool = True
    admin_token: str = ""
    telegram_bot_token: str = ""
    smtp_host: str = "smtp.gmail.com"  # email for sending cleaners their Telegram link
    smtp_port: int = 587
    smtp_user: str = ""              # e.g. yourname@gmail.com
    smtp_password: str = ""          # Gmail: an App Password (needs 2-step verification), not your normal password
    mail_from: str = ""              # defaults to smtp_user
    telegram_bot_username: str = ""   # e.g. Kerkyra_bot; looked up with getMe when empty
    telegram_webhook_secret: str = ""  # set => Telegram calls POST /webhooks/telegram; empty => the app long-polls instead
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    llm_enabled: bool = True
    schedule_xlsx: str = "data/cleaner_schedules.xlsx"  # per-cleaner program, derived from the DB, read by Gemini
    dry_run: bool = True             # print messages instead of sending them

    @classmethod
    def from_env(cls) -> "Settings":
        twilio_token = _s("TWILIO_AUTH_TOKEN")
        viber_token = _s("VIBER_AUTH_TOKEN")
        telegram_token = _s("TELEGRAM_BOT_TOKEN")
        return cls(
            db_path=_s("DB_PATH", "data/agent.db"),
            cleaners_csv=_s("CLEANERS_CSV", "data/cleaners.csv"),
            properties_csv=_s("PROPERTIES_CSV", "data/properties.csv"),
            timezone=_s("TIMEZONE", "Europe/Athens"),
            default_country_code=_s("DEFAULT_COUNTRY_CODE", "+30"),
            sync_interval_min=_i("SYNC_INTERVAL_MIN", 15),
            offer_timeout_min=_i("OFFER_TIMEOUT_MIN", 30),
            offer_lead_days=_i("OFFER_LEAD_DAYS", 14),
            horizon_days=_i("HORIZON_DAYS", 90),
            quiet_start_hour=_i("QUIET_START_HOUR", 21),
            quiet_end_hour=_i("QUIET_END_HOUR", 8),
            reminder_hour=_i("REMINDER_HOUR", 18),
            max_distance_km=_f("MAX_DISTANCE_KM", 0.0),
            default_max_jobs_per_day=_i("DEFAULT_MAX_JOBS_PER_DAY", 3),
            stay_clean_every_days=_i("STAY_CLEAN_EVERY_DAYS", 3),
            stay_clean_time=_s("STAY_CLEAN_TIME", "13:00"),
            cleaning_min=_i("CLEANING_MIN", 60),
            load_penalty_km=_f("LOAD_PENALTY_KM", 3.0),
            default_checkout_time=_s("DEFAULT_CHECKOUT_TIME", "11:00"),
            skip_sunday=_b("SKIP_SUNDAY", True),
            ignore_airbnb_blocks=_b("IGNORE_AIRBNB_BLOCKS", True),
            owner_phone=_s("OWNER_PHONE"),
            owner_channel=_s("OWNER_CHANNEL", "sms"),
            owner_language=_s("OWNER_LANGUAGE", "en"),
            twilio_account_sid=_s("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=twilio_token,
            twilio_from=_s("TWILIO_FROM"),
            viber_auth_token=viber_token,
            viber_sender_name=_s("VIBER_SENDER_NAME", "Cleaning"),
            public_base_url=_s("PUBLIC_BASE_URL").rstrip("/"),
            verify_webhooks=_b("VERIFY_WEBHOOKS", True),
            admin_token=_s("ADMIN_TOKEN"),
            telegram_bot_token=telegram_token,
            smtp_host=_s("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=_i("SMTP_PORT", 587),
            smtp_user=_s("SMTP_USER"),
            smtp_password=_s("SMTP_PASSWORD").replace(" ", ""),  # Google shows app passwords as 4 groups of 4
            mail_from=_s("MAIL_FROM"),
            telegram_bot_username=_s("TELEGRAM_BOT_USERNAME").lstrip("@"),
            telegram_webhook_secret=_s("TELEGRAM_WEBHOOK_SECRET"),
            gemini_api_key=_s("GEMINI_API_KEY"),
            gemini_model=_s("GEMINI_MODEL", "gemini-flash-latest"),
            llm_enabled=_b("LLM_ENABLED", True),
            schedule_xlsx=_s("SCHEDULE_XLSX", "data/cleaner_schedules.xlsx"),
            dry_run=_b("DRY_RUN", not (twilio_token or viber_token or telegram_token)),
        )
