#!/usr/bin/env python3
"""
Skrypt: AI Engineers Daily Brief z X.com → email (SMTP)

Funkcje:
- pobiera najnowsze tweety z X API v2 z wybranych kont,
- filtruje tylko posty z ostatnich 24 godzin,
- sortuje je według ważności (polubienia, reposty, odpowiedzi),
- generuje długi brief mailowy po polsku,
- wysyła mail przez SMTP.

Konfiguracja odbywa się wyłącznie przez zmienne środowiskowe (.env lub systemowe).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from collections import Counter
from typing import Any, Dict, List

import requests
from email.message import EmailMessage
import smtplib

try:
    # Opcjonalne ładowanie pliku .env z bieżącego katalogu (wygodne lokalnie)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # Jeśli python-dotenv nie jest zainstalowany, po prostu ignorujemy
    pass


# ==========================
# Konfiguracja X (Twitter) API v2
# ==========================

# IMPORTANT:
# X_BEARER_TOKEN powinien być ustawiony jako zmienna środowiskowa.
# Przykład w bashu:
#   export X_BEARER_TOKEN="twój_token_z_panelu_X"
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")

# Konta, z których pobieramy tweety (usernames)
X_USERS = [
    "elonmusk",
    "ilyasut",
    "DrJimFan",
    "ylecun",
    "GavinSBaker",
    "Yuchenj_UW",
    "zeeshanp_",
    "demishassabis",
    "daveshap",
]

X_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

# Budujemy zapytanie typu:
# (from:elonmusk OR from:ilyasut OR ...) -is:retweet
X_QUERY = "(" + " OR ".join(f"from:{u}" for u in X_USERS) + ") -is:retweet"

X_SEARCH_PARAMS: Dict[str, str] = {
    "query": X_QUERY,
    "max_results": "100",  # maksymalnie 100 najnowszych tweetów
    "tweet.fields": "created_at,public_metrics,lang",
    "expansions": "author_id",
    "user.fields": "name,username",
}


def require_env(var_name: str) -> str:
    """Pobierz wymaganą zmienną środowiskową lub przerwij skrypt z czytelnym komunikatem."""
    value = os.getenv(var_name)
    if not value:
        print(f"BŁĄD: wymagana zmienna środowiskowa {var_name} nie jest ustawiona.", file=sys.stderr)
        sys.exit(1)
    return value


def fetch_tweets() -> Dict[str, Any]:
    """
    Pobierz najnowsze tweety z X API v2.

    Używamy endpointu /2/tweets/search/recent z autoryzacją Bearer Token.
    Token jest pobierany ze zmiennej środowiskowej X_BEARER_TOKEN.
    """
    if not X_BEARER_TOKEN:
        print("BŁĄD: Zmienna środowiskowa X_BEARER_TOKEN nie jest ustawiona.", file=sys.stderr)
        sys.exit(1)

    headers = {
        # Kluczowy nagłówek autoryzacyjny dla X API v2
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
    }

    response = requests.get(X_SEARCH_URL, headers=headers, params=X_SEARCH_PARAMS, timeout=30)
    if not response.ok:
        print(f"BŁĄD: X API zwróciło kod {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)

    return response.json()


def parse_tweets(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Przetwórz odpowiedź z X API v2 do ujednoliconej listy tweetów.

    Każdy element listy ma pola:
    - author_name
    - author_username
    - created_at (datetime w UTC)
    - text
    - likes
    - retweets
    - replies
    - quotes
    - url
    """
    data = raw.get("data", []) or []
    includes = raw.get("includes", {}) or {}
    users = includes.get("users", []) or []

    user_map: Dict[str, Dict[str, Any]] = {}
    for u in users:
        if "id" in u:
            user_map[u["id"]] = u

    tweets: List[Dict[str, Any]] = []

    for t in data:
        text = t.get("text", "")
        if not text:
            continue

        created_at_str = t.get("created_at")
        if not created_at_str:
            continue

        try:
            # X API stosuje format ISO 8601, np. "2024-02-01T10:23:45.000Z"
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except ValueError:
            # Jeśli format daty jest nieprawidłowy, pomijamy tweeta
            continue

        author_id = t.get("author_id")
        user = user_map.get(author_id, {})
        author_username = user.get("username", author_id or "unknown")
        author_name = user.get("name", author_username)

        metrics = t.get("public_metrics", {}) or {}
        likes = int(metrics.get("like_count", 0))
        retweets = int(metrics.get("retweet_count", 0))
        replies = int(metrics.get("reply_count", 0))
        quotes = int(metrics.get("quote_count", 0))

        tweet_id = t.get("id")
        url = f"https://x.com/i/status/{tweet_id}" if tweet_id else ""

        tweets.append(
            {
                "author_name": author_name,
                "author_username": author_username,
                "created_at": created_at,
                "text": text,
                "likes": likes,
                "retweets": retweets,
                "replies": replies,
                "quotes": quotes,
                "url": url,
            }
        )

    return tweets


def filter_last_24h(tweets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Zostaw tylko tweety z ostatnich 24 godzin (liczone od teraz, w UTC)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    return [t for t in tweets if isinstance(t.get("created_at"), datetime) and t["created_at"] >= cutoff]


def importance_score(tweet: Dict[str, Any]) -> int:
    """
    Prosty score ważności posta.

    Możesz dowolnie zmienić wagi, np. bardziej premiować reposty.
    """
    likes = int(tweet.get("likes", 0))
    retweets = int(tweet.get("retweets", 0))
    replies = int(tweet.get("replies", 0))
    quotes = int(tweet.get("quotes", 0))

    # Wagi: like=3, retweet=2, reply=1, quote=2
    return likes * 3 + retweets * 2 + replies * 1 + quotes * 2


def sort_by_importance(tweets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Posortuj tweety malejąco po ważności, a przy remisie po dacie (nowsze wyżej)."""
    return sorted(
        tweets,
        key=lambda t: (importance_score(t), t.get("created_at") or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )


def shorten_text(text: str, max_len: int = 280) -> str:
    """Skróć tekst do max_len znaków, zachowując całe wyrazy, jeśli to możliwe."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    truncated = cleaned[: max_len - 3]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


def generate_email_body(tweets: List[Dict[str, Any]]) -> str:
    """Wygeneruj długi brief mailowy po polsku w formacie tekst/Markdown."""
    today_str = datetime.now().strftime("%d.%m.%Y")

    if not tweets:
        return (
            f"📬 AI Engineers Daily Brief – {today_str}\n\n"
            "## Brak nowych postów\n\n"
            "W ciągu ostatnich 24 godzin nie znaleziono nowych postów od śledzonych inżynierów AI na X.com.\n"
        )

    lines: List[str] = []
    lines.append(f"📬 AI Engineers Daily Brief – {today_str}")
    lines.append("")
    lines.append("## Najważniejsze posty z ostatnich 24 godzin")
    lines.append("")

    # Statystyki do późniejszego „Podsumowania dnia”
    author_counter: Counter[str] = Counter()
    total_likes = 0
    total_retweets = 0
    total_replies = 0

    for idx, t in enumerate(tweets, start=1):
        author_name = t["author_name"]
        author_username = t["author_username"]
        created_at: datetime = t["created_at"]
        text = t["text"]
        likes = t["likes"]
        retweets = t["retweets"]
        replies = t["replies"]
        url = t["url"]
        score = importance_score(t)

        author_counter[author_name] += 1
        total_likes += likes
        total_retweets += retweets
        total_replies += replies

        created_local_str = created_at.astimezone().strftime("%Y-%m-%d %H:%M")

        lines.append(f"### {idx}. {author_name} (@{author_username})")
        lines.append(f"- **Data**: {created_local_str}")
        lines.append(f"- **Ważność** (score): {score}  |  ❤ {likes}  🔁 {retweets}  💬 {replies}")
        lines.append("")
        lines.append("**Krótki opis posta:**")
        lines.append(shorten_text(text, max_len=400))
        lines.append("")
        if url:
            lines.append(f"[Link do posta]({url})")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Podsumowanie dnia – proste, heurystyczne
    lines.append("## Podsumowanie dnia")
    lines.append("")

    total_posts = len(tweets)
    top_author, top_count = author_counter.most_common(1)[0]

    lines.append(
        f"W ciągu ostatnich 24 godzin zarejestrowano {total_posts} istotnych postów od wybranych inżynierów i badaczy AI."
    )
    lines.append(
        f"Najbardziej aktywny był(a) **{top_author}**, z liczbą {top_count} postów, które wygenerowały łącznie wiele interakcji."
    )
    lines.append(
        f"Łącznie posty zebrały około {total_likes} polubień, {total_retweets} repostów i {total_replies} odpowiedzi, "
        "koncentrując się głównie na tematach związanych z rozwojem modeli, produktami opartymi o AI oraz dyskusjami o przyszłości branży."
    )

    return "\n".join(lines)


# ==========================
# Konfiguracja SMTP (wysyłka maila)
# ==========================

# Wszystkie poniższe dane również powinny być ustawione jako zmienne środowiskowe.
# Możesz je zdefiniować w pliku .env (patrz przykład niżej) lub bezpośrednio w systemie.
SMTP_HOST = os.getenv("SMTP_HOST")  # np. "smtp.gmail.com"
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # 587 dla STARTTLS, 465 dla SSL
SMTP_USER = os.getenv("SMTP_USER")  # login do serwera SMTP
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")  # hasło / app password
SMTP_FROM = os.getenv("SMTP_FROM")  # adres nadawcy
SMTP_TO = os.getenv("SMTP_TO")  # adres odbiorcy (jeden, przecinek lub lista w kodzie)


def send_email(subject: str, body: str) -> None:
    """
    Wyślij mail z użyciem klasycznego SMTP.

    Dane dostępowe są pobierane ze zmiennych środowiskowych:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TO.
    """
    if not all([SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TO]):
        print(
            "BŁĄD: Brakuje jednej z wymaganych zmiennych środowiskowych SMTP_HOST, SMTP_PORT, "
            "SMTP_USER, SMTP_PASSWORD, SMTP_FROM lub SMTP_TO.",
            file=sys.stderr,
        )
        sys.exit(1)

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = SMTP_TO
    msg["Subject"] = subject
    msg.set_content(body)

    # Typowy scenariusz: połączenie na porcie 587 z STARTTLS
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        try:
            server.starttls()
            server.ehlo()
        except smtplib.SMTPException:
            # Jeśli serwer nie obsługuje STARTTLS (np. lokalny relay), kontynuujemy bez szyfrowania
            pass
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def main() -> None:
    # Proaktyczna walidacja kilku kluczowych zmiennych środowiskowych
    require_env("X_BEARER_TOKEN")
    require_env("SMTP_HOST")
    require_env("SMTP_USER")
    require_env("SMTP_PASSWORD")
    require_env("SMTP_FROM")
    require_env("SMTP_TO")

    print("Pobieram tweety z X API v2...")
    raw = fetch_tweets()
    tweets = parse_tweets(raw)
    print(f"Łączna liczba pobranych tweetów: {len(tweets)}")

    recent = filter_last_24h(tweets)
    print(f"Tweety z ostatnich 24 godzin: {len(recent)}")

    sorted_tweets = sort_by_importance(recent)
    body = generate_email_body(sorted_tweets)

    today_str = datetime.now().strftime("%d.%m.%Y")
    subject = f"📬 AI Engineers Daily Brief – {today_str}"

    print("Wysyłam email z podsumowaniem...")
    send_email(subject, body)
    print("Gotowe – email wysłany.")


if __name__ == "__main__":
    main()

