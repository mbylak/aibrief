#!/usr/bin/env python3
"""
Skrypt: AI Engineers Daily Brief z X.com → email (SMTP)

Funkcje:
- pobiera najnowsze tweety z X.com przez RSS feeds (RSSHub),
- filtruje tylko posty z ostatnich 24 godzin,
- sortuje je według ważności (daty publikacji, długości treści),
- generuje długi brief mailowy po polsku,
- wysyła mail przez SMTP.

Konfiguracja odbywa się wyłącznie przez zmienne środowiskowe (.env lub systemowe).
"""

import os
import sys
import re
from datetime import datetime, timedelta, timezone
from collections import Counter
from typing import Any, Dict, List
from urllib.parse import urlparse, parse_qs

import requests
import feedparser
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
# Konfiguracja RSS feeds dla X.com
# ==========================

# Używamy RSSHub jako źródła RSS feedów dla X.com
# RSSHub to darmowy serwis, który generuje RSS z różnych platform, w tym X.com
# Alternatywnie możesz użyć własnej instancji RSSHub lub innych serwisów
RSSHUB_BASE_URL = os.getenv("RSSHUB_BASE_URL", "https://rsshub.app")

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

# Mapowanie username -> pełne imię (dla lepszej czytelności w briefie)
AUTHOR_NAMES = {
    "elonmusk": "Elon Musk",
    "ilyasut": "Ilya Sutskever",
    "DrJimFan": "Dr Jim Fan",
    "ylecun": "Yann LeCun",
    "GavinSBaker": "Gavin Baker",
    "Yuchenj_UW": "Yuchen Jin",
    "zeeshanp_": "Zeeshan Patel",
    "demishassabis": "Demis Hassabis",
    "daveshap": "David Shapiro",
}


def require_env(var_name: str) -> str:
    """Pobierz wymaganą zmienną środowiskową lub przerwij skrypt z czytelnym komunikatem."""
    value = os.getenv(var_name)
    if not value:
        print(f"BŁĄD: wymagana zmienna środowiskowa {var_name} nie jest ustawiona.", file=sys.stderr)
        sys.exit(1)
    return value


def fetch_tweets_from_rss() -> List[Dict[str, Any]]:
    """
    Pobierz najnowsze tweety z X.com przez RSS feeds (RSSHub).

    RSSHub generuje RSS feed dla każdego użytkownika X.com.
    Endpoint: https://rsshub.app/twitter/user/USERNAME

    Zwraca listę wszystkich tweetów ze wszystkich kont.
    """
    all_tweets: List[Dict[str, Any]] = []

    for username in X_USERS:
        rss_url = f"{RSSHUB_BASE_URL}/twitter/user/{username}"
        print(f"Pobieram RSS dla @{username}...", end=" ")

        try:
            # RSSHub może wymagać User-Agent, dodajemy go dla pewności
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = requests.get(rss_url, headers=headers, timeout=30)

            if not response.ok:
                print(f"BŁĄD: RSSHub zwróciło kod {response.status_code} dla @{username}")
                continue

            # Parsuj RSS feed
            feed = feedparser.parse(response.content)

            if feed.bozo:
                print(f"BŁĄD: Nieprawidłowy format RSS dla @{username}")
                continue

            # Przetwórz każdy wpis z feeda
            for entry in feed.entries:
                # RSSHub zwykle zawiera link do tweeta w formacie:
                # https://twitter.com/USERNAME/status/TWEET_ID
                # lub https://x.com/USERNAME/status/TWEET_ID
                link = entry.get("link", "")
                tweet_id = extract_tweet_id_from_url(link)

                # Parsuj datę publikacji
                published_time = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published_time = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass

                # Jeśli nie ma parsed date, spróbuj z published string
                if not published_time and hasattr(entry, "published"):
                    try:
                        # feedparser czasami parsuje daty automatycznie
                        if hasattr(entry, "published_parsed"):
                            published_time = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                        else:
                            # Fallback: spróbuj sparsować ręcznie
                            published_time = datetime.now(timezone.utc)
                    except:
                        published_time = datetime.now(timezone.utc)

                if not published_time:
                    published_time = datetime.now(timezone.utc)

                # Tekst tweeta - może być w title lub summary
                text = entry.get("title", "") or entry.get("summary", "")
                # Usuń HTML tags jeśli są
                text = re.sub(r"<[^>]+>", "", text)
                text = text.strip()

                if not text:
                    continue

                # Autor - może być w author lub wyciągnięty z linku/tytułu
                author_name = AUTHOR_NAMES.get(username, username)
                author_username = username

                all_tweets.append(
                    {
                        "author_name": author_name,
                        "author_username": author_username,
                        "created_at": published_time,
                        "text": text,
                        "likes": 0,  # RSS nie zawiera metryk
                        "retweets": 0,
                        "replies": 0,
                        "quotes": 0,
                        "url": link if link else f"https://x.com/{username}/status/{tweet_id}" if tweet_id else "",
                    }
                )

            print(f"OK ({len(feed.entries)} tweetów)")
        except Exception as e:
            print(f"BŁĄD: {e}")
            continue

    return all_tweets


def extract_tweet_id_from_url(url: str) -> str:
    """Wyciągnij ID tweeta z URL (np. z https://x.com/user/status/123456789)."""
    if not url:
        return ""
    # Próbuj wyciągnąć ID z różnych formatów URL
    patterns = [
        r"/status/(\d+)",
        r"status/(\d+)",
        r"tweet_id=(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


# Funkcja parse_tweets() nie jest już potrzebna - fetch_tweets_from_rss() zwraca już gotową listę


def filter_last_24h(tweets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Zostaw tylko tweety z ostatnich 24 godzin (liczone od teraz, w UTC)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    return [t for t in tweets if isinstance(t.get("created_at"), datetime) and t["created_at"] >= cutoff]


def importance_score(tweet: Dict[str, Any]) -> int:
    """
    Prosty score ważności posta.

    Ponieważ RSS nie zawiera metryk (likes, retweets), używamy heurystyki:
    - Dłuższe tweety są często bardziej wartościowe
    - Nowsze tweety są ważniejsze
    - Możemy też użyć długości tekstu jako proxy dla zaangażowania
    """
    text = tweet.get("text", "")
    text_length = len(text)

    # Podstawowy score oparty na długości (dłuższe = ważniejsze)
    # Dodajemy też bonus za "nowość" (ale to już jest w sortowaniu)
    base_score = min(text_length // 10, 100)  # max 100 punktów za długość

    # Bonus za zawartość słów kluczowych związanych z AI/ML
    ai_keywords = ["ai", "ml", "model", "neural", "llm", "gpt", "training", "research", "paper"]
    text_lower = text.lower()
    keyword_bonus = sum(10 for keyword in ai_keywords if keyword in text_lower)

    return base_score + keyword_bonus


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
        # RSS nie zawiera metryk, więc pomijamy wyświetlanie likes/retweets jeśli są zerowe
        if likes > 0 or retweets > 0 or replies > 0:
            lines.append(f"- **Ważność** (score): {score}  |  ❤ {likes}  🔁 {retweets}  💬 {replies}")
        else:
            lines.append(f"- **Ważność** (score): {score}")
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
        f"Najbardziej aktywny był(a) **{top_author}**, z liczbą {top_count} postów."
    )
    if total_likes > 0 or total_retweets > 0 or total_replies > 0:
        lines.append(
            f"Łącznie posty zebrały około {total_likes} polubień, {total_retweets} repostów i {total_replies} odpowiedzi, "
            "koncentrując się głównie na tematach związanych z rozwojem modeli, produktami opartymi o AI oraz dyskusjami o przyszłości branży."
        )
    else:
        lines.append(
            "Posty koncentrują się głównie na tematach związanych z rozwojem modeli, produktami opartymi o AI oraz dyskusjami o przyszłości branży."
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
    # Proaktyczna walidacja kluczowych zmiennych środowiskowych (tylko SMTP, X_BEARER_TOKEN nie jest już potrzebny)
    require_env("SMTP_HOST")
    require_env("SMTP_USER")
    require_env("SMTP_PASSWORD")
    require_env("SMTP_FROM")
    require_env("SMTP_TO")

    print("Pobieram tweety z X.com przez RSS feeds (RSSHub)...")
    tweets = fetch_tweets_from_rss()
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

