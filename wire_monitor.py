"""Wire monitor z Gemini i Wariant B filter.

Sesja 2a sub-step 2: full pipeline.
GlobeNewswire RSS -> body fetch -> Gemini analiza -> Finnhub consensus -> filter -> Telegram.
"""
import datetime
import json
import os
import re
import sys
from pathlib import Path

import feedparser
import requests
from google import genai

import watchlist


GLOBENEWSWIRE_EARNINGS_FEED = (
    "https://www.globenewswire.com/AtomFeed/subjectcode/"
    "13-Earnings%20Releases%20And%20Operating%20Results/"
    "feedTitle/GlobeNewswire%20-%20Earnings%20Releases%20And%20Operating%20Results"
)
FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
STATE_FILE = Path("state.json")
MAX_STATE_ENTRIES = 1000
GEMINI_MODEL = "gemini-2.5-flash"

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Earnings Monitor Bot)"}


def load_state():
    if not STATE_FILE.exists():
        return {"processed_ids": []}
    with STATE_FILE.open() as f:
        return json.load(f)


def save_state(state):
    state["processed_ids"] = state["processed_ids"][-MAX_STATE_ENTRIES:]
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def fetch_globenewswire_earnings():
    print("Pobieranie feedu GlobeNewswire earnings...")
    feed = feedparser.parse(GLOBENEWSWIRE_EARNINGS_FEED)
    if feed.bozo:
        print(f"OSTRZEZENIE feed parser: {feed.bozo_exception}")
    print(f"Pobrano {len(feed.entries)} wpisow.")
    return feed.entries


def fetch_press_release_body(url):
    """Pobiera HTML press release i strippuje tagi do czystego tekstu."""
    response = requests.get(url, timeout=30, headers=HTTP_HEADERS)
    response.raise_for_status()
    html = response.text
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]  # limit kontekstu Gemini


def analyze_with_gemini(client, press_release_text):
    """Wysyla press release do Gemini, parsuje JSON z odpowiedzi."""
    prompt = f"""Przeanalizuj ten earnings press release i zwroc CZYSTY JSON (bez markdown).

Format:
{{
  "ticker": "TICKER spolki (sam symbol, bez exchange prefix)",
  "company_name": "Pelna nazwa spolki",
  "eps_actual": liczba (np. 1.15) lub null,
  "eps_consensus_in_text": liczba lub null,
  "revenue_actual": liczba w pelnych USD (np. 5000000000) lub null,
  "revenue_consensus_in_text": liczba w pelnych USD lub null,
  "guidance_change": jedno z ["raised","lowered","maintained","none"],
  "guidance_note": "krotka notatka po polsku max 100 znakow, lub pusty string"
}}

Wazne:
- Jesli wartosci nie ma w tekscie - daj null. Nie zmysłaj.
- Revenue w USD (jesli spolka raportuje w innej walucie - daj null).
- guidance_change="raised" gdy podnosi prognoze, "lowered" gdy obniza, "maintained" gdy potwierdza, "none" gdy brak wzmianki.

Press release:
{press_release_text}
"""
    text = ""
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()
        # Strip markdown code fences jesli Gemini je dorzucil mimo instrukcji
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.rstrip().endswith("```"):
                text = text.rsplit("```", 1)[0]
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        print(f"  BLAD parsowania JSON od Gemini: {e}")
        print(f"  Raw response (pierwsze 300 znakow): {text[:300]}")
        return None
    except Exception as e:
        print(f"  BLAD wywolania Gemini: {e}")
        return None


def fetch_consensus_from_finnhub(ticker):
    """Pobiera consensus EPS/revenue dla tickera (zakres +/- 1 dzien)."""
    finnhub_token = os.environ.get("FINNHUB_TOKEN")
    if not finnhub_token:
        return None
    today = datetime.date.today()
    params = {
        "from": (today - datetime.timedelta(days=1)).isoformat(),
        "to": (today + datetime.timedelta(days=1)).isoformat(),
        "symbol": ticker,
        "token": finnhub_token,
    }
    try:
        r = requests.get(FINNHUB_EARNINGS_URL, params=params, timeout=30)
        r.raise_for_status()
        entries = r.json().get("earningsCalendar", [])
        if not entries:
            return None
        e = entries[0]
        return {
            "eps_consensus": e.get("epsEstimate"),
            "revenue_consensus": e.get("revenueEstimate"),
        }
    except Exception as e:
        print(f"  BLAD Finnhub: {e}")
        return None


def calculate_surprise_pct(actual, consensus):
    if actual is None or consensus is None or consensus == 0:
        return None
    return ((actual - consensus) / abs(consensus)) * 100.0


def should_alert(ticker, eps_surprise, rev_surprise, guidance_change, revenue_consensus):
    """Wariant B filter."""
    is_watchlist = ticker in watchlist.WATCHLIST
    if is_watchlist:
        eps_thr = watchlist.WATCHLIST_EPS_THRESHOLD
        rev_thr = watchlist.WATCHLIST_REV_THRESHOLD
    else:
        if revenue_consensus is None or revenue_consensus < watchlist.MIN_REVENUE_FOR_GENERAL:
            return False
        eps_thr = watchlist.GENERAL_EPS_THRESHOLD
        rev_thr = watchlist.GENERAL_REV_THRESHOLD

    if eps_surprise is not None and abs(eps_surprise) > eps_thr:
        return True
    if rev_surprise is not None and abs(rev_surprise) > rev_thr:
        return True
    if guidance_change in ("raised", "lowered"):
        return True
    return False


def format_money(value):
    if value is None:
        return "brak"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.0f}M"
    return f"${value:.0f}"


def build_alert_message(ticker, analysis, consensus_data, eps_surprise, rev_surprise, source_url):
    is_watchlist = ticker in watchlist.WATCHLIST
    star = "⭐ " if is_watchlist else ""
    company = analysis.get("company_name", ticker)

    lines = [f"{star}📈 EARNINGS — {ticker}", company, ""]

    eps_actual = analysis.get("eps_actual")
    eps_consensus = consensus_data.get("eps_consensus") if consensus_data else None
    if eps_actual is not None:
        line = f"EPS: ${eps_actual:.2f}"
        if eps_surprise is not None and eps_consensus is not None:
            sign = "+" if eps_surprise > 0 else ""
            badge = "🟢 BEAT" if eps_surprise > 0 else "🔴 MISS"
            line += f" vs ${eps_consensus:.2f} ({sign}{eps_surprise:.1f}%) {badge}"
        lines.append(line)

    rev_actual = analysis.get("revenue_actual")
    rev_consensus = consensus_data.get("revenue_consensus") if consensus_data else None
    if rev_actual is not None:
        line = f"Revenue: {format_money(rev_actual)}"
        if rev_surprise is not None and rev_consensus is not None:
            sign = "+" if rev_surprise > 0 else ""
            badge = "🟢 BEAT" if rev_surprise > 0 else "🔴 MISS"
            line += f" vs {format_money(rev_consensus)} ({sign}{rev_surprise:.1f}%) {badge}"
        lines.append(line)

    guidance = analysis.get("guidance_change")
    if guidance in ("raised", "lowered"):
        emoji = "🚀" if guidance == "raised" else "⚠️"
        label = "PODNIESIONY" if guidance == "raised" else "OBNIZONY"
        note = (analysis.get("guidance_note") or "").strip()
        gline = f"{emoji} Guidance {label}"
        if note:
            gline += f": {note}"
        lines.append(gline)

    lines.append("")
    lines.append(f"🔗 {source_url}")
    return "\n".join(lines)


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url, json={"chat_id": chat_id, "text": message}, timeout=30
    )
    response.raise_for_status()


def main():
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    gemini_api_key = os.environ.get("GEMINI_API_KEY")

    missing = [k for k, v in [
        ("TELEGRAM_BOT_TOKEN", telegram_token),
        ("TELEGRAM_CHAT_ID", chat_id),
        ("GEMINI_API_KEY", gemini_api_key),
    ] if not v]
    if missing:
        print(f"BLAD: brakuje secrets: {', '.join(missing)}")
        sys.exit(1)

    gemini_client = genai.Client(api_key=gemini_api_key)

    state = load_state()
    processed = set(state.get("processed_ids", []))
    print(f"State: {len(processed)} przetworzonych entry IDs.")

    entries = fetch_globenewswire_earnings()

    new_alerts = 0
    skipped_filter = 0
    skipped_error = 0

    for entry in entries:
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id or entry_id in processed:
            continue

        print(f"\n--- ANALIZA: {entry.title[:100]}")
        processed.add(entry_id)  # mark even on failure (avoid retry loop)

        try:
            body = fetch_press_release_body(entry.link)
            print(f"  Body length: {len(body)} chars")
        except Exception as e:
            print(f"  BLAD pobierania body: {e}")
            skipped_error += 1
            continue

        analysis = analyze_with_gemini(gemini_client, body)
        if not analysis or not analysis.get("ticker"):
            print(f"  Pominieto: brak analizy Gemini lub brak tickera")
            skipped_error += 1
            continue

        ticker = analysis["ticker"].upper()
        print(f"  Ticker: {ticker}, EPS: {analysis.get('eps_actual')}, "
              f"Revenue: {analysis.get('revenue_actual')}, "
              f"Guidance: {analysis.get('guidance_change')}")

        consensus_data = fetch_consensus_from_finnhub(ticker)
        if consensus_data:
            print(f"  Consensus: EPS {consensus_data.get('eps_consensus')}, "
                  f"Rev {consensus_data.get('revenue_consensus')}")
        else:
            print(f"  Brak consensus z Finnhub dla {ticker}")

        eps_surprise = calculate_surprise_pct(
            analysis.get("eps_actual"),
            consensus_data.get("eps_consensus") if consensus_data else None,
        )
        rev_surprise = calculate_surprise_pct(
            analysis.get("revenue_actual"),
            consensus_data.get("revenue_consensus") if consensus_data else None,
        )

        revenue_consensus_for_filter = (
            consensus_data.get("revenue_consensus") if consensus_data else None
        )

        if should_alert(
            ticker, eps_surprise, rev_surprise,
            analysis.get("guidance_change"),
            revenue_consensus_for_filter,
        ):
            message = build_alert_message(
                ticker, analysis, consensus_data,
                eps_surprise, rev_surprise, entry.link,
            )
            send_telegram(telegram_token, chat_id, message)
            new_alerts += 1
            print(f"  ✅ ALERT WYSLANY")
        else:
            skipped_filter += 1
            print(f"  ❌ Pominieto: nie przeszlo Wariant B filter")

    state["processed_ids"] = list(processed)
    save_state(state)

    print(
        f"\n=== PODSUMOWANIE ===\n"
        f"Alerty wyslane: {new_alerts}\n"
        f"Pominiete (filter): {skipped_filter}\n"
        f"Pominiete (bledy): {skipped_error}"
    )


if __name__ == "__main__":
    main()
