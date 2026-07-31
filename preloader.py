"""
CineVault Pre-loader
=====================
Automatically fetches top Tamil/Hindi movies from TMDB
and saves them to DB1 channel + MongoDB using bot_fetcher.
Runs 24/7 in background.
"""

import os
import sys
import asyncio
import aiohttp
import motor.motor_asyncio
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from bot_fetcher import smart_fetch

# ── Config ─────────────────────────────────────────────────────────────────
API_ID   = 31654968
API_HASH = 'b00f22e26a8c38db4172ce84f7d96ae2'
HARDCODED_SESSION = (
    "1BVtsOLkBu5JeC5sPJ_3ZAay5Xlhypv-6MSBYCjeaXb6PswozZwlkaoBJm1_xqFkkqsT5rznnbt0-"
    "0O79dRxM87wc2ZWWI8ZvsWGkcmteEgAWCyX_n7F4iESRiUA7lqpLHqawrxj8fR8GYs7Kkd4mwtrhTo_"
    "sFoyT5tUoACMuPWL9UTZc1QToIR1VYTR3Arbbw113nzorwflmVQIT0oDoZ-YjbAJEQxoCYp7JZrsq-"
    "iwVc0kdFgDw8a35CBzPnqTeLfjRl4lBV182IFtS_ne2LAT-pi8jDcKy7RdAsHjeFbXqAGofFbolkJxav"
    "HvX3aVgSAm8InBFysOzggO-nZie0smGZiD1_iw="
)
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING', HARDCODED_SESSION)
TMDB_API_KEY   = "74683f7b34f7b689d84fcd8e0016d82a"
MONGO_URI      = "mongodb+srv://freeforwebsitein_db_user:PQWHxVtcA1NnOqVO@cluster0.knj55zu.mongodb.net"

DELAY_BETWEEN = 30   # seconds between each movie fetch
TMDB_BASE     = "https://api.tmdb.org/3"

# Movie lists to pre-load (TMDB endpoints)
FETCH_LISTS = [
    ("Tamil Popular",   f"{TMDB_BASE}/discover/movie?with_original_language=ta&sort_by=popularity.desc&region=IN"),
    ("Tamil Top Rated", f"{TMDB_BASE}/discover/movie?with_original_language=ta&sort_by=vote_average.desc&vote_count.gte=500&region=IN"),
    ("Hindi Popular",   f"{TMDB_BASE}/discover/movie?with_original_language=hi&sort_by=popularity.desc&region=IN"),
    ("Trending",        f"{TMDB_BASE}/trending/movie/week?region=IN"),
]


async def get_tmdb_movies(session: aiohttp.ClientSession, url: str, pages: int = 3) -> list:
    """Fetch movies from TMDB API across multiple pages."""
    movies = []
    for page in range(1, pages + 1):
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}api_key={TMDB_API_KEY}&language=ta-IN&page={page}"
        try:
            async with session.get(full_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    movies.extend(data.get("results", []))
                    print(f"  [OK] Fetched page {page}: {len(data.get('results', []))} movies")
        except Exception as e:
            print(f"  [-] TMDB fetch error: {e}")
    return movies


async def is_already_saved(mongo_db, title: str, year: int) -> bool:
    """Check if this movie is already in MongoDB."""
    if mongo_db is None:
        return False
    count = await mongo_db.movies.count_documents({"title": title, "year": year})
    return count > 0


async def run_preloader():
    """Main pre-loader loop — fetches top movies and saves them all."""
    # MongoDB
    mongo_db = None
    if MONGO_URI:
        mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        mongo_db = mongo_client["cinevault"]
        print("[OK] Connected to MongoDB")

    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        print("[OK] Connected to Telegram!")

        async with aiohttp.ClientSession() as http:
            for list_name, url in FETCH_LISTS:
                print(f"\n{'='*50}")
                print(f"[*] Starting list: {list_name}")
                print(f"{'='*50}")

                movies = await get_tmdb_movies(http, url, pages=5)
                print(f"[*] Total movies to process: {len(movies)}")

                success = 0
                skip    = 0
                fail    = 0

                for i, movie in enumerate(movies):
                    title = movie.get("title", "")
                    year_raw = movie.get("release_date", "")[:4]
                    year  = int(year_raw) if year_raw.isdigit() else 0

                    if not title:
                        continue

                    print(f"\n[{i+1}/{len(movies)}] Processing: {title} ({year})")

                    # Skip if already in DB
                    if await is_already_saved(mongo_db, title, year):
                        print(f"  [SKIP] Already in database!")
                        skip += 1
                        continue

                    # Fetch using smart_fetch (imdbot + movie bots)
                    query = f"{title} {year}" if year else title
                    result = await smart_fetch(
                        client,
                        query=query,
                        is_series=False,
                        mongo_db=mongo_db,
                    )

                    if result:
                        print(f"  [OK] SUCCESS: {title} saved!")
                        success += 1
                    else:
                        print(f"  [-] FAILED: {title}")
                        fail += 1

                    # Wait between fetches to avoid bot rate limits
                    await asyncio.sleep(DELAY_BETWEEN)

                print(f"\n[*] List '{list_name}' done!")
                print(f"    Success: {success} | Skipped: {skip} | Failed: {fail}")

        print("\n[OK] Pre-loader complete! All lists processed.")


if __name__ == "__main__":
    asyncio.run(run_preloader())
