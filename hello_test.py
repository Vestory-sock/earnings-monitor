"""Test plumbingu — wysyła wiadomość testową do Telegrama."""
import os
import sys
import requests


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("BLAD: brakuje TELEGRAM_BOT_TOKEN lub TELEGRAM_CHAT_ID.")
        sys.exit(1)

    message = (
        "🚀 Earnings Monitor — test plumbingu\n\n"
        "Jesli widzisz te wiadomosc, secrets dzialaja poprawnie "
        "i bot jest gotowy do dalszej konfiguracji."
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": message})

    if response.status_code == 200:
        print("OK: wiadomosc wyslana pomyslnie.")
    else:
        print(f"BLAD: HTTP {response.status_code}")
        print(response.text)
        sys.exit(1)


if __name__ == "__main__":
    main()
