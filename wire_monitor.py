"""Wire monitor — multi-source (GlobeNewswire + SEC EDGAR per watchlist).

Sesja 2b: dodaje SEC EDGAR jako drugie zrodlo dla watchlist mega-capow.
Cold-start protection: nowe zrodla najpierw seedują state.json bez analizy.
"""
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests
from google import genai

import watchlist


# === SOURCE: GlobeNewswire ===
GLOBENEWSWIRE_EARNINGS_FEED = (
    "https://www.globenewswire.com/AtomFeed/subjectcode/"
    "13-Earnings%20Releases%20And%20Operating%20Results/"
    "feedTitle/GlobeNewswire%20-%20Earnings%20Releases%20And%20Operating%20Results"
)

# === SOURCE: SEC EDGAR per watchlist ===
EDGAR_FEED_TPL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?"
    "action=getcompany&CIK={cik}&type=8-K&dateb=&owner=include&count=10&output=atom"
)
EDGAR_HEADERS = {
    "User-Agent": "Earnings Monitor Bot (vestory-sock github)",
    "Accept-Encoding": "gzip, deflate",
}

# === Finnhub consensus ===
FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"

# === Storage ===
STATE_FILE = Path("state.json")
MAX_STATE_ENTRIES = 3000
GEMINI_MODEL = "gemini-2.5-flash"
POLL_INTERVAL_SEC = 20

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Earnings Monitor Bot)"}


def determine_runtime_minutes():
    now = datetime.datetime.utcnow()
    if now.weekday() >= 5:
        return 0
    hour, minute = now.hour, now.minute
    if (hour == 19 and minute >= 45) or hour in (20, 21) or (hour == 22 and minute <= 30):
        return 40
    if (hour == 10 and minute >= 25) or hour in (11, 12) or (hour == 13 and minute <= 30):
        return 90
    return 0


def load_state():
    if not STATE_FILE.exists():
        return {"processed_ids": [], "seeded_sources": []}
    with STATE_FILE.open() as f:
        state = json.load(f)
    # Migracja: stary format mial tylko processed_ids -> auto-seed globenewswire
    if "processed_ids" in state and "seeded_sources" not in state:
        state["seeded_sources"] = ["globenewswire"] if state["processed_ids"] else []
    state.setdefault("processed_ids", [])
    state.setdefault("seeded_sources", [])
    return state


def save_state(state):
    state["processed_ids"] = state["processed_ids"][-MAX_STATE_ENTRIES:]
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


# === FEED FETCHERS ===

def fetch_globenewswire_entries():
    feed = feedparser.parse(GLOBENEWSWIRE_EARNINGS_FEED)
    if feed.bozo:
        print(f"  OSTRZEZENIE GNW feed: {feed.bozo_exception}")
    return feed.entries


def fetch_edgar_entries(ticker, cik):
    url = EDGAR_FEED_TPL.format(cik=cik)
    try:
        r = requests.get(url, headers=EDGAR_HEADERS, timeout=30)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        return feed.entries
    except Exception as e:
        print(f"  BLAD EDGAR feed dla {ticker}: {e}")
        return []


def fetch_all_sources():
    """Zwraca liste (source_id, source_type, entry, ticker_hint)."""
    out = []
    try:
        for entry in fetch_globenewswire_entries():
            out.append(("globenewswire", "wire", entry, None))
    except Exception as e:
        print(f"BLAD GlobeNewswire: {e}")
    for ticker, cik in watchlist.WATCHLIST_CIKS.items():
        source_id = f"edgar-{ticker}"
        for entry in fetch_edgar_entries(ticker, cik):
            out.append((source_id, "edgar", entry, ticker))
    return out


# === BODY FETCHERS ===

def _strip_html(html):
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_press_release_body(url):
    response = requests.get(url, timeout=30, headers=HTTP_HEADERS)
    response.raise_for_status()
    return _strip_html(response.text)[:8000]


def fetch_edgar_filing_body(index_url):
    """Pobiera EDGAR 8-K index, znajduje EX-99.1 (press release) lub glowny dok."""
    try:
        r = requests.get(index_url, headers=EDGAR_HEADERS, timeout=30)
        r.raise_for_status()
        index_html = r.text
    except Exception as e:
        print(f"  BLAD EDGAR index: {e}")
        return ""

    links = re.findall(
        r'<a[^>]+href="(/Archives/edgar/[^"]+\.htm)"', index_html, re.IGNORECASE
    )
    if not links:
        return ""

    # Wolimy ex99 (press release ze szczegolami earnings)
    doc_url = None
    for path in links:
        lower = path.lower()
        if ("ex99" in lower or "ex-99" in lower or "exhibit99" in lower) and "index" not in lower:
            doc_url = "https://www.sec.gov" + path
            break
    # Fallback: pierwszy .htm ktory nie jest index
    if not doc_url:
        for path in links:
            if "index" not in path.lower():
                doc_url = "https://www.sec.gov" + path
                break
    if not doc_url:
        return ""

    try:
        r = requests.get(doc_url, headers=EDGAR_HEADERS, timeout=30)
        r.raise_for_status()
        return _strip_html(r.text)[:8000]
    except Exception as e:
        print(f"  BLAD EDGAR document: {e}")
        return ""


# === ANALYSIS ===

def analyze_with_gemini(client, press_release_text):
    prompt = f"""Przeanalizuj ten earnings press release lub 8-K filing i zwroc CZYSTY JSON (bez markdown).

Format:
{{
  "ticker": "TICKER spolki (sam symbol) lub null jesli to NIE earnings",
  "company_name": "Pelna nazwa",
  "eps_actual": liczba lub null,
  "eps_consensus_in_text": liczba lub null,
  "revenue_actual": liczba w pelnych USD lub null,
  "revenue_consensus_in_text": liczba w pelnych USD lub null,
  "guidance_change": jedno z ["raised","lowered","maintained","none"],
  "guidance_note": "krotka notatka po polsku max 100 znakow lub pusty string"
}}

Wazne:
- Jesli to NIE jest komunikat o wynikach finansowych (np. zmiana zarzadu, M&A, FDA approval, dividend declaration) - daj ticker=null
- Jesli wartosci nie ma w tekscie - daj null. Nie zmysłaj.
- Revenue w USD (jesli spolka raportuje w innej walucie - daj null).

Tekst:
{press_release_text}
"""
    text = ""
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.rstrip().endswith("```"):
                text = text.rsplit("```", 1)[0]
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        print(f"  BLAD JSON od Gemini: {e}")
        print(f"  Raw: {text[:300]}")
        return None
    except Exception as e:
        print(f"  BLAD Gemini: {e}")
        return None


def fetch_consensus_from_finnhub(ticker):
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


def build_alert_message(ticker, analysis, consensus_data, eps_surprise, rev_surprise,
                        source_url, source_label):
    is_watchlist = ticker in watchlist.WATCHLIST
    star = "⭐ " if is_watchlist else ""
    company = analysis.get("company_name", ticker)
    lines = [f"{star}📈 EARNINGS — {ticker}", company, f"źródło: {source_label}", ""]

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
    response = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=30)
    response.raise_for_status()


def run_one_pass(telegram_token, chat_id, gemini_client, state):
    processed = set(state.get("processed_ids", []))
    seeded_sources = set(state.get("seeded_sources", []))
    counters = {"new_alerts": 0, "skipped_filter": 0, "skipped_error": 0,
                "processed": 0, "seeded": 0}

    all_entries = fetch_all_sources()
    sources_seen = set()

    for source_id, source_type, entry, ticker_hint in all_entries:
        sources_seen.add(source_id)
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id or entry_id in processed:
            continue

        processed.add(entry_id)

        # Seed mode: zrodlo jeszcze nie zaseedowane → tylko zapisz ID
        if source_id not in seeded_sources:
            counters["seeded"] += 1
            continue

        counters["processed"] += 1
        title_short = (entry.get("title", "") or "")[:100]
        print(f"\n--- [{source_id}] {title_short}")

        try:
            if source_type == "edgar":
                body = fetch_edgar_filing_body(entry.link)
            else:
                body = fetch_press_release_body(entry.link)
        except Exception as e:
            print(f"  BLAD body: {e}")
            counters["skipped_error"] += 1
            continue

        if not body or len(body) < 200:
            print(f"  Pominieto: body za krotkie ({len(body)} chars)")
            counters["skipped_error"] += 1
            continue

        print(f"  Body: {len(body)} chars")
        analysis = analyze_with_gemini(gemini_client, body)
        if not analysis or not analysis.get("ticker"):
            print(f"  Pominieto: brak tickera (moze nie earnings)")
            counters["skipped_error"] += 1
            continue

        # EDGAR: ufamy ticker_hint bardziej niz Gemini
        if source_type == "edgar" and ticker_hint:
            if analysis["ticker"].upper() != ticker_hint:
                print(f"  Override: Gemini={analysis['ticker']} → {ticker_hint}")
                analysis["ticker"] = ticker_hint

        ticker = analysis["ticker"].upper()
        consensus_data = fetch_consensus_from_finnhub(ticker)
        eps_surprise = calculate_surprise_pct(
            analysis.get("eps_actual"),
            consensus_data.get("eps_consensus") if consensus_data else None,
        )
        rev_surprise = calculate_surprise_pct(
            analysis.get("revenue_actual"),
            consensus_data.get("revenue_consensus") if consensus_data else None,
        )
        rev_consensus_for_filter = (
            consensus_data.get("revenue_consensus") if consensus_data else None
        )

        if should_alert(ticker, eps_surprise, rev_surprise,
                       analysis.get("guidance_change"), rev_consensus_for_filter):
            source_label = "EDGAR 8-K" if source_type == "edgar" else "GlobeNewswire"
            message = build_alert_message(
                ticker, analysis, consensus_data,
                eps_surprise, rev_surprise, entry.link, source_label,
            )
            send_telegram(telegram_token, chat_id, message)
            counters["new_alerts"] += 1
            print(f"  ✅ ALERT [{ticker}]")
        else:
            counters["skipped_filter"] += 1
            print(f"  ❌ Pominieto przez filter")

    seeded_sources.update(sources_seen)
    state["processed_ids"] = list(processed)
    state["seeded_sources"] = list(seeded_sources)
    return counters


def print_summary(counters, prefix=""):
    print(f"\n{prefix}=== PODSUMOWANIE ===")
    print(f"Przetworzonych: {counters['processed']}")
    print(f"Zaseedowanych (bez analizy): {counters['seeded']}")
    print(f"Alerty wyslane: {counters['new_alerts']}")
    print(f"Pominiete (filter): {counters['skipped_filter']}")
    print(f"Pominiete (bledy): {counters['skipped_error']}")


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

    runtime_min = determine_runtime_minutes()
    state = load_state()
    print(f"State: {len(state.get('processed_ids', []))} IDs, "
          f"seeded: {state.get('seeded_sources', [])}")
    print(f"Tryb: {'long polling ' + str(runtime_min) + ' min' if runtime_min > 0 else 'single pass'}")

    if runtime_min == 0:
        counters = run_one_pass(telegram_token, chat_id, gemini_client, state)
        save_state(state)
        print_summary(counters)
        return

    deadline = time.time() + runtime_min * 60
    iteration = 0
    total = {"new_alerts": 0, "skipped_filter": 0, "skipped_error": 0,
             "processed": 0, "seeded": 0}

    while time.time() < deadline:
        iteration += 1
        print(f"\n========== ITERACJA {iteration} ==========")
        counters = run_one_pass(telegram_token, chat_id, gemini_client, state)
        for k in total:
            total[k] += counters[k]
        save_state(state)
        remaining = deadline - time.time()
        if remaining <= POLL_INTERVAL_SEC:
            break
        print(f"Iter {iteration} koniec. Czekam {POLL_INTERVAL_SEC}s. Pozostalo {remaining:.0f}s.")
        time.sleep(POLL_INTERVAL_SEC)

    print(f"\n========== KONIEC (iteracji: {iteration}) ==========")
    print_summary(total, prefix="ŁĄCZNE ")


if __name__ == "__main__":
    main()
