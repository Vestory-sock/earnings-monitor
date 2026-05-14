"""Daily earnings digest - wysyla codzienna liste spolek raportujacych dzisiaj."""
import datetime
import os
import sys
import requests


FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"


def fetch_earnings_today(token):
    """Pobiera kalendarz earnings z Finnhub dla dzisiejszej daty (UTC)."""
    today = datetime.date.today()
    params = {
        "from": today.isoformat(),
        "to": today.isoformat(),
        "token": token,
    }
    response = requests.get(FINNHUB_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("earningsCalendar", [])


def format_money(value):
    """Formatuje revenue z literka B/M."""
    if value is None:
        return "brak"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.0f}M"
    return f"${value:.0f}"


def format_eps(value):
    """Formatuje EPS (zwykle 2 miejsca po przecinku)."""
    if value is None:
        return "brak"
    return f"${value:.2f}"


def build_message(earnings):
    """Buduje wiadomosc po polsku z listy earnings."""
    today = datetime.date.today()
    polish_months = {
        1: "stycznia", 2: "lutego", 3: "marca", 4: "kwietnia",
        5: "maja", 6: "czerwca", 7: "lipca", 8: "sierpnia",
        9: "września", 10: "października", 11: "listopada", 12: "grudnia",
    }
    date_str = f"{today.day} {polish_months[today.month]} {today.year}"

    if not earnings:
        return f"📅 EARNINGS — {date_str}\n\nDziś brak zaplanowanych publikacji wyników."

    bmo = [e for e in earnings if e.get("hour") == "bmo"]
    amc = [e for e in earnings if e.get("hour") == "amc"]
    other = [e for e in earnings if e.get("hour") not in ("bmo", "amc")]

    # Sortuj malejaco po revenue estimate (najwieksi pierwsi)
    sort_key = lambda e: -(e.get("revenueEstimate") or 0)
    bmo.sort(key=sort_key)
    amc.sort(key=sort_key)

    lines = [f"📅 EARNINGS — {date_str}\n"]

    if bmo:
        lines.append(f"🟢 Przed otwarciem (BMO, ~13:00-15:30 PL) — {len(bmo)} spółek:")
        for e in bmo[:15]:
            ticker = e.get("symbol", "?")
            eps = format_eps(e.get("epsEstimate"))
            rev = format_money(e.get("revenueEstimate"))
            lines.append(f"• {ticker} — EPS {eps}, rev {rev}")
        if len(bmo) > 15:
            lines.append(f"... i {len(bmo) - 15} innych BMO")
        lines.append("")

    if amc:
        lines.append(f"🔴 Po zamknięciu (AMC, ~22:00-22:30 PL) — {len(amc)} spółek:")
        for e in amc[:15]:
            ticker = e.get("symbol", "?")
            eps = format_eps(e.get("epsEstimate"))
            rev = format_money(e.get("revenueEstimate"))
            lines.append(f"• {ticker} — EPS {eps}, rev {rev}")
        if len(amc) > 15:
            lines.append(f"... i {len(amc) - 15} innych AMC")
        lines.append("")

    if other:
        lines.append(f"⚪ Inne (godzina nieokreślona) — {len(other)} spółek")

    return "\n".join(lines)


def send_telegram(token, chat_id, message):
    """Wysyla wiadomosc do Telegrama."""
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

    print("Pobieranie earnings z Finnhub...")
    earnings = fetch_earnings_today(finnhub_token)
    print(f"Pobrano {len(earnings)} wpisow.")

    message = build_message(earnings)
    print("Tresc wiadomosci:")
    print(message)
    print("---")

    print("Wysylanie do Telegrama...")
    send_telegram(telegram_token, chat_id, message)
    print("OK: daily digest wyslany.")


if __name__ == "__main__":
    main()
