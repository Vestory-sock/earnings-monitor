"""Wire monitor - sledzi feed earnings z GlobeNewswire i alertuje dla watchlisty.

Sesja 2a sub-step 1: feed -> watchlist match -> Telegram alert.
Analiza Gemini + Wariant B dodamy w sub-step 2.
"""
import json
import os
import re
import sys
from pathlib import Path

import feedparser
import requests

import watchlist


GLOBENEWSWIRE_EARNINGS_FEED = (
    "https://www.globenewswire.com/AtomFeed/subjectcode/"
    "13-Earnings%20Releases%20And%20Operating%20Results/"
    "feedTitle/GlobeNewswire%20-%20Earnings%20Releases%20And%20Operating%20Results"
)

STATE_FILE = Path("state.json")
MAX_STATE_ENTRIES = 1000  # zeby state.json nie rosl w nieskonczonosc


def load_state():
    if not STATE_FILE.exists():
        return {"processed_ids": []}
    with STATE_FILE.open() as f:
        return json.load(f)


def save_state(state):
    state["processed_ids"] = state["processed_ids"][-MAX_STATE_ENTRIES:]
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def extract_tickers_from_title(title):
    """Wyciaga tickery z formatu '(NASDAQ: ABCD)' / '(NYSE: ABCD)' w tytule."""
    pattern = r"\((?:NYSE|NASDAQ|NYSE American|AMEX|TSX|TSXV|OTCQB|OTCQX)[^:)]*:\s*([A-Z\.]+)\)"
    return set(re.findall(pattern, title))


def fetch_globenewswire_earnings():
    print("Pobieranie feedu GlobeNewswire earnings...")
    feed = feedparser.parse(GLOBENEWSWIRE_EARNINGS_FEED)
    if feed.bozo:
        print(f"OSTRZEZENIE feed parser: {feed.bozo_exception}")
    print(f"Pobrano {len(feed.entries)} wpisow.")
    return feed.entries


def build_alert_message(entry, matched_tickers):
    tickers_str = ", ".join(sorted(matched_tickers))
    published = entry.get("published", entry.get("updated", "brak daty"))
    return (
        f"📈 EARNINGS — {tickers_str}\n\n"
        f"{entry.title}\n\n"
        f"🔗 {entry.link}\n"
        f"🕐 {published}\n\n"
        f"(GlobeNewswire. Analiza Gemini + beat/miss w kolejnej iteracji.)"
    )


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url, json={"chat_id": chat_id, "text": message}, timeout=30
    )
    response.raise_for_status()


def main():
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not telegram_token or not chat_id:
        print("BLAD: brakuje TELEGRAM_BOT_TOKEN lub TELEGRAM_CHAT_ID.")
        sys.exit(1)

    state = load_state()
    processed = set(state.get("processed_ids", []))
    print(f"State: {len(processed)} przetworzonych entry IDs juz w pamieci.")

    entries = fetch_globenewswire_earnings()

    # DEBUG: pokaz wszystkie tytuly zeby zobaczyc format
    print("\n=== TYTULY W FEEDZIE ===")
    for entry in entries:
        print(f"- {entry.title}")
    print("=== /TYTULY ===\n")
    
    new_alerts = 0
    skipped_no_match = 0
    skipped_no_ticker = 0

    for entry in entries:
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id or entry_id in processed:
            continue

        tickers = extract_tickers_from_title(entry.title)

        if not tickers:
            skipped_no_ticker += 1
            processed.add(entry_id)
            continue

        matched = tickers & watchlist.WATCHLIST

        if matched:
            print(f"ALERT (watchlist match {matched}): {entry.title}")
            message = build_alert_message(entry, matched)
            send_telegram(telegram_token, chat_id, message)
            new_alerts += 1
        else:
            skipped_no_match += 1
            print(f"Skip (tickery {tickers} poza watchlista): {entry.title[:80]}")

        processed.add(entry_id)

    state["processed_ids"] = list(processed)
    save_state(state)

    print(
        f"\nPodsumowanie: {new_alerts} alertow, "
        f"{skipped_no_match} z tickerami poza watchlista, "
        f"{skipped_no_ticker} bez wyciagnietych tickerow."
    )


if __name__ == "__main__":
    main()
