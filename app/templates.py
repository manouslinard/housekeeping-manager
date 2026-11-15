"""Message texts (English and Greek). Plain ASCII-friendly wording keeps SMS segments cheap."""
from __future__ import annotations

from datetime import date

EN = {
    "offer": "Hi {name}! Cleaning job #{id}: {property} ({address}), {date}, from {time}.{turnover} About {km} km from you. Reply YES {id} to accept or NO {id} to decline.",
    "turnover": " SAME-DAY turnover: the next guest arrives that day.",
    "stay": " Mid-stay cleaning: the guest is staying, tidy up and change towels.",
    "confirmed": "Confirmed! Job #{id}: {property} ({address}) on {date} from {time}. Reply DONE {id} when you finish.",
    "declined": "No problem, thanks for letting us know.",
    "taken": "Job #{id} is no longer available. Thanks anyway!",
    "cancelled": "Update: job #{id} ({property}, {date}) was cancelled. Thanks!",
    "full": "You already have another job at that time or the maximum number of jobs that day, so job #{id} goes to someone else. Thanks!",
    "reminder": "Reminder: tomorrow ({date}) you clean {property} ({address}) from {time}.{turnover} Reply DONE {id} when you finish.",
    "done_ack": "Thanks! Job #{id} marked as done.",
    "done_unknown": "I couldn't find a job to mark as done. Reply DONE followed by the job number.",
    "no_open": "You have no open job offers right now.",
    "ambiguous": "You have several open offers. Please include the job number, e.g. YES {id}.",
    "unknown": "Sorry, I didn't understand. Reply YES <job#> to accept, NO <job#> to decline, or DONE <job#> when finished.",
    "linked": "Thanks {name}, you're linked! Job offers will arrive here.",
    "link_prompt": "Hi! Please send your phone number exactly as registered with us (for example +306912345678) so we can link this chat.",
    "link_expired": "This link was already used or is old. Ask for a new one, or send your phone number (e.g. +306912345678).",
    "tg_linked_hint": "Open the bot and tap Start, then send your phone number.",
    "unavailable_ack": "Noted, you are unavailable {frm} - {to}. Thanks for telling us, we'll arrange the jobs.",
    "rearranged": "OK, thanks, we'll arrange it.",
    "owner_assigned": "Job #{id} {property} ({date}): {cleaner} confirmed.",
    "owner_unfilled": "ALERT: no cleaner found for job #{id} {property}, checkout {date}. Add cleaners to cleaners.csv, then call /jobs/{id}/retry.",
    "owner_cancelled": "Job #{id} {property} ({date}) was cancelled because the booking disappeared from the calendar. {cleaner} has been told.",
    "owner_done": "Job #{id} {property} ({date}) marked done by {cleaner}",
}

EL = {
    "offer": "Γεια σου {name}! Νέο καθάρισμα #{id}: {property} ({address}), {date} από τις {time}.{turnover} Απόσταση ~{km} χλμ. Απάντησε ΝΑΙ {id} για αποδοχή ή ΟΧΙ {id} για απόρριψη.",
    "turnover": " ΠΡΟΣΟΧΗ: την ίδια μέρα έρχεται νέος επισκέπτης.",
    "stay": " Ενδιάμεσος καθαρισμός: ο επισκέπτης μένει ακόμη, τακτοποίηση και πετσέτες.",
    "confirmed": "Επιβεβαιώθηκε! Καθάρισμα #{id}: {property} ({address}), {date} από τις {time}. Απάντησε ΤΕΛΟΣ {id} όταν τελειώσεις.",
    "declined": "Κανένα πρόβλημα, ευχαριστούμε που μας ενημέρωσες.",
    "taken": "Το καθάρισμα #{id} δεν είναι πλέον διαθέσιμο. Ευχαριστούμε!",
    "cancelled": "Ενημέρωση: το καθάρισμα #{id} ({property}, {date}) ακυρώθηκε. Ευχαριστούμε!",
    "full": "Έχεις ήδη άλλο καθάρισμα εκείνη την ώρα ή το μέγιστο αριθμό για εκείνη την ημέρα, οπότε το #{id} πάει σε άλλον. Ευχαριστούμε!",
    "reminder": "Υπενθύμιση: αύριο ({date}) καθαρίζεις {property} ({address}) από τις {time}.{turnover} Απάντησε ΤΕΛΟΣ {id} όταν τελειώσεις.",
    "done_ack": "Ευχαριστούμε! Το καθάρισμα #{id} σημειώθηκε ως ολοκληρωμένο.",
    "done_unknown": "Δεν βρήκα καθάρισμα για ολοκλήρωση. Απάντησε ΤΕΛΟΣ και τον αριθμό.",
    "no_open": "Δεν έχεις ανοιχτές προσφορές αυτή τη στιγμή.",
    "ambiguous": "Έχεις περισσότερες από μία προσφορές. Γράψε και τον αριθμό, π.χ. ΝΑΙ {id}.",
    "unknown": "Συγγνώμη, δεν κατάλαβα. Απάντησε ΝΑΙ <αριθμός> για αποδοχή, ΟΧΙ <αριθμός> για απόρριψη ή ΤΕΛΟΣ <αριθμός> όταν τελειώσεις.",
    "linked": "Ευχαριστούμε {name}, συνδέθηκες! Οι προσφορές θα έρχονται εδώ.",
    "link_prompt": "Γεια σου! Στείλε τον αριθμό τηλεφώνου σου όπως είναι δηλωμένος σε εμάς (π.χ. +306912345678) για να συνδέσουμε τη συνομιλία.",
    "link_expired": "Αυτός ο σύνδεσμος έχει ήδη χρησιμοποιηθεί ή είναι παλιός. Ζήτα νέο, ή στείλε τον αριθμό τηλεφώνου σου (π.χ. +306912345678).",
    "tg_linked_hint": "Άνοιξε το bot, πάτα Start και στείλε τον αριθμό τηλεφώνου σου.",
    "unavailable_ack": "Σημειώθηκε, δεν είσαι διαθέσιμη {frm} - {to}. Ευχαριστούμε που μας ενημέρωσες, θα τακτοποιήσουμε τα καθαρίσματα.",
    "rearranged": "Εντάξει, ευχαριστούμε, θα το τακτοποιήσουμε.",
    "owner_assigned": "Καθάρισμα #{id} {property} ({date}): το επιβεβαίωσε ο/η {cleaner}.",
    "owner_unfilled": "ΠΡΟΣΟΧΗ: δεν βρέθηκε καθαριστής για το #{id} {property}, check-out {date}. Πρόσθεσε καθαριστές στο cleaners.csv και κάλεσε /jobs/{id}/retry.",
    "owner_cancelled": "Το καθάρισμα #{id} {property} ({date}) ακυρώθηκε γιατί η κράτηση εξαφανίστηκε από το ημερολόγιο. Ενημερώθηκε ο/η {cleaner}.",
    "owner_done": "Το καθάρισμα #{id} {property} ({date}) ολοκληρώθηκε από τον/την {cleaner}",
}

STRINGS = {"en": EN, "el": EL}

_DAYS = {
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "el": ["Δευ", "Τρί", "Τετ", "Πέμ", "Παρ", "Σάβ", "Κυρ"],
}
_MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "el": ["Ιαν", "Φεβ", "Μαρ", "Απρ", "Μαΐ", "Ιουν", "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ"],
}


def fmt_date(d: date, lang: str = "en") -> str:
    lang = lang if lang in _DAYS else "en"
    return f"{_DAYS[lang][d.weekday()]} {d.day} {_MONTHS[lang][d.month - 1]}"


def t(lang: str, key: str, **kw) -> str:
    table = STRINGS.get(lang, EN)
    return (table.get(key) or EN[key]).format(**kw)
