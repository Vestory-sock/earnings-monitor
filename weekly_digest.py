"""Weekly earnings digest - wysyla podsumowanie spolek raportujacych w biezacym tygodniu."""
import datetime
import os
import sys
from collections import defaultdict
import requests


FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"

POLISH_MONTHS = {
    1: "stycznia", 2: "lutego", 3: "marca", 4: "kwietnia",
    5: "maja", 6: "czerwca", 7: "lipca", 8: "sierpnia",
    9: "września", 10: "października", 11: "listopada", 12: "grudnia",
}

POLISH_WEEKDAYS = {
    0: "Poniedziałek", 1: "Wtorek", 2: "Środa", 3: "Czwartek",
    4: "Piątek", 5: "Sobota", 6: "Niedziela",
}


def fetch_earnings(start_date, end_date, token):
    params = {
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "token": token,
    }
    response = requests.get(FINNHUB_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("earningsCalendar", [])


def format_money(value):
    if value is None:
        return "brak"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.0f}M"
    return f"${value:.0f}"


def build_message(earnings, monday, sunday):
    week_str = (
        f"{monday.day} {POLISH_MONTHS[monday.month]} - "
        f"{sunday.day} {POLISH_MONTHS[sunday.month]} {sunday.year}"
    )

    if not earnings:
        return f"📅 EARNINGS — tydzień {week_str}\n\nBrak zaplanowanych publikacji w tym tygodniu."

    by_date = defaultdict(list)
    for e in earnings:
        date_str = e.get("date")
        if date_str:
            by_date[date_str].append(e)

    lines = [f"📅 EARNINGS — tydzień {week_str}\n"]

    current = monday
    while current <= sunday:
        date_key = current.isoformat()
        day_entries = by_date.get(date_key, [])

        if not day_entries:
            current += datetime.timedelta(days=1)
            continue

        weekday = POLISH_WEEKDAYS[current.weekday()]
        day_name = f"{weekday.upper()} {current.day} {POLISH_MONTHS[current.month]}"

        # Sortuj malejaco po revenue estimate
        day_entries.sort(key=lambda e: -(e.get("revenueEstimate") or 0))

        # Filtruj do >$500M revenue (large caps)
        big = [e for e in day_entries if (e.get("revenueEstimate") or 0) >= 500_000_000]

        lines.append(f"\n— {day_name} —")

        if not big:
            lines.append(f"({len(day_entries)} spółek, wszystkie mid/small cap)")
        else:
            for e in big[:8]:
                ticker = e.get("symbol", "?")
                hour = e.get("hour", "")
                hour_label = {"bmo": "BMO", "amc": "AMC"}.get(hour, "")
                hour_str = f" [{hour_label}]" if hour_label else ""
                rev = format_money(e.get("revenueEstimate"))
                lines.append(f"• {ticker}{hour_str} — rev {rev}")

            if len(big) > 8:
                lines.append(f"... i {len(big) - 8} innych large-cap")

            small_count = len(day_entries) - len(big)
            if small_count > 0:
                lines.append(f"(+ {small_count} mid/small cap)")

        current += datetime.timedelta(days=1)

    lines.append("\n💡 Codzienny szczegółowy digest co rano 8:00 PL")
    return "\n".join(lines)


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url, json={"chat_id": chat_id, "text": message}, timeout=30
    )
    response.raise_for_status()


def main():
    finnhub_token = os.environ.get("FINNHUB_TOKEN")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing = [name for name, val in [
        ("FINNHUB_TOKEN", finnhub_token),
        ("TELEGRAM_BOT_TOKEN", telegram_token),
        ("TELEGRAM_CHAT_ID", chat_id),
    ] if not val]
    if missing:
        print(f"BLAD: brakuje secrets: {', '.join(missing)}")
        sys.exit(1)

    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    print(f"Tygodniowy zakres: {monday} - {sunday}")

    print("Pobieranie earnings z Finnhub...")
    earnings = fetch_earnings(monday, sunday, finnhub_token)
    print(f"Pobrano {len(earnings)} wpisow w tygodniu.")

    message = build_message(earnings, monday, sunday)
    print("Tresc wiadomosci:")
    print(message)
    print("---")

    print("Wysylanie do Telegrama...")
    send_telegram(telegram_token, chat_id, message)
    print("OK: weekly digest wyslany.")


if __name__ == "__main__":
    main()
