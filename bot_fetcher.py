"""
CineVault Bot Fetcher — Smart Edition
======================================
Full flow:
  1. User search query -> @imdbot -> correct title, year, poster, rating, cast
  2. Corrected title + year -> @ProSearchFilesBot / @TVSeriesSearchBot -> file
  3. Forward file to DB1 channel
  4. Save full metadata to MongoDB
"""

import os
import re
import sys
import asyncio
import motor.motor_asyncio
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    KeyboardButtonCallback,
    ReplyInlineMarkup,
)

# Fix Windows console encoding issue with special characters
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


# ── Config ────────────────────────────────────────────────────────────────────
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

DB1_CHANNEL      = "https://t.me/+I9jiBz3SjvRlNjNl"
IMDB_BOT         = "@imdbot"

# Bot priority order — best quality first, fallback if fails
MOVIE_BOTS  = ["@Aiv2trbot", "@ProSearchFilesBot"]   # Aiv2trbot = best quality (needs 4h verify)
SERIES_BOTS = ["@Aiv2trbot", "@TVSeriesSearchBot"]   # same bot works for series too

QUALITY_PRIORITY = ["2160", "1080", "720", "480", "360"]

# How to check if @Aiv2trbot is verified:
# If bot replies with "YOU'RE NOT VERIFIED" -> skip to next bot
AIV2TR_NOT_VERIFIED_TEXT = "NOT VERIFIED"

MONGO_URI = "mongodb+srv://freeforwebsitein_db_user:PQWHxVtcA1NnOqVO@cluster0.knj55zu.mongodb.net"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — @imdbot: Get correct metadata from IMDB
# ─────────────────────────────────────────────────────────────────────────────

async def get_imdb_metadata(client: TelegramClient, query: str) -> dict | None:
    """
    @imdbot is an INLINE bot. Use client.inline_query() to search,
    then send the result to Saved Messages and read it back.
    """
    print(f"\n[*] Querying @imdbot (inline) for: '{query}'")
    try:
        # Inline query to @imdbot — InlineResults is directly iterable
        results = await client.inline_query(IMDB_BOT, query)
        results_list = list(results)

        if not results_list:
            print("  [-] @imdbot returned no results")
            return None

        print(f"  [OK] Got {len(results_list)} results from @imdbot")

        # Try to pick the best match:
        # 1. Prefer Tamil/Indian cast results (Joseph Vijay, etc.)
        # 2. Otherwise pick the first result
        chosen = results_list[0]
        for r in results_list:
            title_str = (getattr(r, 'title', '') or '').lower()
            desc_str  = (getattr(r, 'description', '') or '').lower()
            combined  = title_str + " " + desc_str
            # Prefer results with Indian actors
            if any(name in combined for name in
                   ['vijay', 'rajini', 'ajith', 'kamal', 'dhanush',
                    'suriya', 'allu', 'prabhas', 'ram charan']):
                chosen = r
                print(f"  [OK] Picked Tamil result: {getattr(r, 'title', '')}")
                break

        if chosen == results_list[0]:
            print(f"  [OK] Using first result: {getattr(chosen, 'title', '')}")

        # Send chosen result to Saved Messages (self)
        me = await client.get_me()
        await chosen.click(me.id)
        print(f"  [OK] Sent to Saved Messages")
        await asyncio.sleep(3)

        # Read the message back from Saved Messages
        msgs = await client.get_messages(me.id, limit=5)
        reply = None
        for m in msgs:
            if m.text and ("Movie:" in m.text or "Rating" in m.text or
                           "Genre" in m.text or "via @imdbot" in m.text):
                reply = m
                break

        if not reply:
            # Fallback: build metadata from inline result title/description
            print("  [-] Using inline result title as fallback metadata")
            title_raw = getattr(chosen, 'title', query)
            # Match year with OR without parentheses: "Leo (2023)" OR "Leo 2023"
            year_m = re.search(r'[\(\s](\d{4})[\)\s]?$', title_raw.strip())
            if not year_m:
                year_m = re.search(r'\b(\d{4})\b', title_raw)
            year_val = int(year_m.group(1)) if year_m else 0
            # Remove year from title cleanly
            clean_title = re.sub(r'\s*[\(]?\d{4}[\)]?\s*$', '', title_raw).strip()
            desc = getattr(chosen, 'description', '')
            print(f"  [OK] Fallback metadata: '{clean_title}' ({year_val})")
            return {
                'title':       clean_title,
                'year':        year_val,
                'rating':      0.0,
                'runtime':     'N/A',
                'genres':      '',
                'language':    'Tamil',
                'description': desc,
                'director':    '',
                'stars':       desc,
                'has_poster':  False,
            }

        text = reply.text or ""
        print(f"  [OK] Got IMDB info:\n{text[:300]}...")


        # Read the posted message from Saved Messages
        msgs = await client.get_messages(me.id, limit=5)
        reply = None
        for m in msgs:
            if m.text and ("Movie:" in m.text or "Rating" in m.text or
                           "Genre" in m.text or "via @imdbot" in m.text):
                reply = m
                break

        if not reply:
            # Fallback: extract title/year from inline result directly
            print("  [-] Could not find message in Saved Messages, using inline result title")
            title_raw = getattr(first, 'title', query)
            year_m = re.search(r'\((\d{4})\)', title_raw)
            return {
                'title':       re.sub(r'\s*\(\d{4}\)', '', title_raw).strip(),
                'year':        int(year_m.group(1)) if year_m else 0,
                'rating':      0.0,
                'runtime':     'N/A',
                'genres':      '',
                'language':    'Tamil',
                'description': getattr(first, 'description', ''),
                'director':    '',
                'stars':       '',
                'has_poster':  False,
            }

        text = reply.text or ""
        print(f"  [OK] Got IMDB reply:\n{text[:300]}...")

        # ── Parse fields from the bot's response ─────────────────────────────
        # Example format:
        # Movie: Leo [2023]
        # Rating ⭐ 7.2/10
        # Release Info: 21/11/2023 ...
        # Genre: #Action #Crime #Drama #Thriller
        # Language: #Tamil
        # Story Line: ...
        # Directors: ...
        # Stars: ...

        metadata = {}

        # Strip markdown formatting before parsing
        # e.g. "**Movie**: [Leo](url) [2023]" -> "Movie: Leo [2023]"
        clean = re.sub(r'\*+', '', text)                          # remove **bold**
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)   # [text](url) -> text
        clean = re.sub(r'`([^`]+)`', r'\1', clean)               # `code` -> code
        clean = re.sub(r'__([^_]+)__', r'\1', clean)             # __italic__ -> italic

        # Title + Year  e.g. "Movie: Leo [2023]"
        title_match = re.search(r'Movie:\s*(.+?)\s*\[(\d{4})\]', clean, re.IGNORECASE)
        if not title_match:
            title_match = re.search(r'(?:Also Known As|Movie):\s*(.+?)\s*[\[\(](\d{4})[\]\)]', clean, re.IGNORECASE)
        if title_match:
            raw_title = title_match.group(1).strip()
            # Clean any remaining markdown from the captured title
            raw_title = re.sub(r'[\[\]\(\)\*_`]', '', raw_title).strip()
            metadata['title'] = raw_title
            metadata['year']  = int(title_match.group(2))
        else:
            # Fallback: use the original query
            metadata['title'] = query
            metadata['year']  = 0

        # Rating  ->  "7.2 based on 74522..."  OR  "⭐ 7.2/10"
        rating_match = re.search(r'(\d+\.\d+)\s*/\s*10', text)
        if not rating_match:
            rating_match = re.search(r'⭐\s*(\d+\.?\d*)', text)
        metadata['rating'] = float(rating_match.group(1)) if rating_match else 0.0

        # Runtime  ->  "2h 44min"
        runtime_match = re.search(r'(\d+h\s*\d*min|\d+\s*min)', text, re.IGNORECASE)
        metadata['runtime'] = runtime_match.group(1).strip() if runtime_match else "N/A"

        # Genre  ->  "#Action #Crime #Drama"
        genres = re.findall(r'#([A-Za-z]+)', text)
        # Filter out language/country tags
        non_genre = {'tamil', 'hindi', 'telugu', 'malayalam', 'india', 'english'}
        genre_list = [g for g in genres if g.lower() not in non_genre]
        metadata['genres'] = ", ".join(genre_list[:4]) if genre_list else "Unknown"

        # Language  ->  "#Tamil"
        lang_match = re.search(r'Language:\s*#(\w+)', text, re.IGNORECASE)
        if not lang_match:
            # Try to find language from hashtags
            for lang in ['Tamil', 'Hindi', 'Telugu', 'Malayalam', 'English']:
                if f'#{lang}' in text:
                    lang_match = type('m', (), {'group': lambda self, n: lang})()
                    break
        metadata['language'] = lang_match.group(1) if lang_match else "Unknown"

        # Description / Story Line
        story_match = re.search(
            r'(?:Story Line|Storyline|Overview):\s*(.+?)(?:\n|Directors?:|Stars?:|$)',
            text, re.IGNORECASE | re.DOTALL
        )
        metadata['description'] = story_match.group(1).strip()[:500] if story_match else ""

        # Director
        dir_match = re.search(r'Directors?:\s*(.+?)(?:\n|Writers?:|Stars?:|$)', text, re.IGNORECASE)
        metadata['director'] = dir_match.group(1).strip() if dir_match else ""

        # Stars
        stars_match = re.search(r'Stars?:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
        metadata['stars'] = stars_match.group(1).strip() if stars_match else ""

        # Poster — the bot usually attaches a photo
        if reply.photo or (reply.media and isinstance(reply.media, MessageMediaPhoto)):
            metadata['has_poster'] = True
            metadata['poster_message'] = reply
        else:
            metadata['has_poster'] = False

        print(f"  [[OK]] Parsed: '{metadata['title']}' ({metadata['year']}) "
              f"| {metadata['rating']}/10 | {metadata['language']}")
        return metadata

    except Exception as e:
        print(f"  [-] @imdbot error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Movie/Series bots: Fetch the file
# ─────────────────────────────────────────────────────────────────────────────

def _best_quality_button(markup):
    if not markup or not isinstance(markup, ReplyInlineMarkup):
        return None
    all_btns = [btn for row in markup.rows for btn in row.buttons
                if isinstance(btn, KeyboardButtonCallback)]
    for q in QUALITY_PRIORITY:
        for btn in all_btns:
            if q in btn.text:
                return btn
    return all_btns[0] if all_btns else None


def _first_result_button(markup):
    """Return the first REAL result button, skipping header/navigation buttons."""
    if not markup or not isinstance(markup, ReplyInlineMarkup):
        return None
    SKIP_LABELS = [
        'quality', 'language', 'season', 'page', 'next', 'prev',
        'select option', 'filter', 'back', 'home', 'menu', 'close',
    ]
    all_btns = [btn for row in markup.rows for btn in row.buttons
                if isinstance(btn, KeyboardButtonCallback)]
    for btn in all_btns:
        label = btn.text.strip().lower()
        # Skip navigation/header labels
        if any(skip in label for skip in SKIP_LABELS):
            continue
        # Skip pagination like "1/16", "2/108"
        if re.match(r'^[\U0001F300-\U0001FFFF\s]*\d+/\d+[\s\U0001F300-\U0001FFFF]*$', btn.text.strip()):
            continue
        # Skip very short labels with no digits (likely category headers)
        if len(label) < 4 and not any(c.isdigit() for c in label):
            continue
        return btn
    return all_btns[0] if all_btns else None


def _best_quality_button(markup):
    """Pick best quality button, skipping ZIP/RAR files and preferring video quality."""
    if not markup or not isinstance(markup, ReplyInlineMarkup):
        return None
    all_btns = [btn for row in markup.rows for btn in row.buttons
                if isinstance(btn, KeyboardButtonCallback)]
    # Filter out ZIP/RAR/torrent files
    video_btns = [btn for btn in all_btns
                  if not any(ext in btn.text.lower() for ext in ['.zip', '.rar', '.torrent', 'zip', 'rar'])]
    search_btns = video_btns if video_btns else all_btns
    # Pick best resolution
    for q in QUALITY_PRIORITY:
        for btn in search_btns:
            if q in btn.text:
                return btn
    return search_btns[0] if search_btns else None


async def _wait_for_message(client, bot_entity, predicate, timeout=30):
    """Poll bot messages until predicate(msg) is True or timeout."""
    for _ in range(timeout):
        await asyncio.sleep(1)
        msgs = await client.get_messages(bot_entity, limit=5)
        for m in msgs:
            if not m.out and predicate(m):
                return m
    return None


async def fetch_file_from_bots(
    client: TelegramClient,
    title: str,
    year: int,
    is_series: bool = False,
) -> object | None:
    """
    Send 'title year' to movie/series bots and return the Telegram file message.
    """
    # Build query — only include year if it's valid
    query = f"{title} {year}" if year and year > 0 else title
    bots  = SERIES_BOTS if is_series else MOVIE_BOTS
    print(f"\n[*] Fetching file for: '{query}' (series={is_series})")

    for bot_username in bots:
        print(f"  [->] Trying {bot_username}...")
        try:
            bot = await client.get_entity(bot_username)
            await client.send_message(bot, query)

            # Wait for any reply
            reply = await _wait_for_message(
                client, bot,
                lambda m: bool(m.reply_markup or m.media or m.text),
                timeout=20
            )
            if not reply:
                print(f"  [-] No reply from {bot_username}")
                continue

            # ── Check if @Aiv2trbot says "NOT VERIFIED" -> skip ───────────────
            if reply.text and AIV2TR_NOT_VERIFIED_TEXT in reply.text.upper():
                print(f"  [!] {bot_username} — NOT VERIFIED. Need to verify first.")
                print(f"  [!] Open Telegram -> @Aiv2trbot -> click 'CLICK HERE TO VERIFY' -> verify once -> 4 hours access!")
                continue  # try next bot

            file_msg = None

            # Case A: File came directly
            if reply.media and isinstance(reply.media, MessageMediaDocument):
                file_msg = reply

            # Case B: Results list -> click first -> quality -> file
            elif reply.reply_markup and not (reply.media and isinstance(reply.media, MessageMediaDocument)):
                # Click first result
                first_btn = _first_result_button(reply.reply_markup)
                if first_btn:
                    print(f"  [*] Clicking first result: {first_btn.text[:40]}")
                    await reply.click(data=first_btn.data)
                    await asyncio.sleep(2)

                # Wait for quality selection
                quality_reply = await _wait_for_message(
                    client, bot,
                    lambda m: bool(m.reply_markup),
                    timeout=15
                )

                if quality_reply:
                    best_btn = _best_quality_button(quality_reply.reply_markup)
                    if best_btn:
                        print(f"  [*] Picking quality: {best_btn.text}")
                        await quality_reply.click(data=best_btn.data)
                        await asyncio.sleep(2)

                # Wait for file
                file_msg = await _wait_for_message(
                    client, bot,
                    lambda m: bool(m.media and isinstance(m.media, MessageMediaDocument)),
                    timeout=30
                )

            if file_msg:
                print(f"  [[OK]] Got file from {bot_username}!")
                return file_msg
            else:
                print(f"  [-] No file received from {bot_username}")

        except Exception as e:
            print(f"  [-] Error with {bot_username}: {e}")
            continue

    print(f"[-] All bots failed for: {query}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3+4 — Forward to DB1 + Save to MongoDB
# ─────────────────────────────────────────────────────────────────────────────

async def forward_and_save(
    client: TelegramClient,
    file_msg,
    metadata: dict,
    db1_entity,
    mongo_db=None,
) -> dict | None:
    """Forward file to DB1 channel and save metadata to MongoDB."""

    # Detect quality from filename or message
    quality = "Unknown"
    combined = (file_msg.text or "") + str(getattr(file_msg.file, 'name', '') or "")
    for q in ["2160", "1080", "720", "480", "360"]:
        if q in combined:
            quality = f"{q}p"
            break

    title    = metadata.get('title', 'Unknown')
    year     = metadata.get('year', 0)
    language = metadata.get('language', 'Unknown')
    rating   = metadata.get('rating', 0.0)
    genres   = metadata.get('genres', '')
    runtime  = metadata.get('runtime', 'N/A')
    desc     = metadata.get('description', '')
    director = metadata.get('director', '')
    stars    = metadata.get('stars', '')

    caption = (
        f"🎬 **{title} ({year})**\n\n"
        f"⭐ Rating: {rating}/10\n"
        f"💿 Quality: {quality}\n"
        f"🗣 Language: {language}\n"
        f"🎭 Genre: {genres}\n"
        f"⏱ Runtime: {runtime}\n"
        f"🎥 Director: {director}\n"
        f"🌟 Stars: {stars[:100]}\n\n"
        f"📖 {desc[:200]}"
    )

    try:
        forwarded = await client.send_message(
            db1_entity,
            file=file_msg.media,
            message=caption,
        )
        print(f"  [[OK]] Forwarded to DB1! Message ID: {forwarded.id}")

        doc = {
            "title":               title,
            "year":                year,
            "quality":             quality,
            "language":            language,
            "rating":              rating,
            "genres":              genres,
            "runtime":             runtime,
            "description":         desc,
            "director":            director,
            "stars":               stars,
            "telegram_message_id": forwarded.id,
            "telegram_channel":    DB1_CHANNEL,
            "file_id":             str(file_msg.media.document.id),
            "timestamp":           datetime.utcnow(),
        }

        if mongo_db is not None:
            try:
                await mongo_db.movies.update_one(
                    {"title": title, "year": year, "quality": quality, "language": language},
                    {"$set": doc},
                    upsert=True,
                )
                print("  [[OK]] Saved to MongoDB!")
            except Exception as e:
                print(f"  [-] MongoDB save failed: {e}")

        return doc

    except Exception as e:
        print(f"  [-] Forward failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PUBLIC FUNCTION — called from main.py /fetch endpoint
# ─────────────────────────────────────────────────────────────────────────────

async def smart_fetch(
    client: TelegramClient,
    query: str,
    is_series: bool = False,
    mongo_db=None,
) -> dict | None:
    """
    Full smart fetch flow:
      query (any spelling) -> @imdbot -> correct title/year/metadata
                           -> movie bot  -> file
                           -> DB1        -> MongoDB
    Returns saved doc or None.
    """
    # Get DB1 entity
    try:
        db1_entity = await client.get_entity(DB1_CHANNEL)
    except Exception as e:
        print(f"[!] Cannot access DB1: {e}")
        return None

    # Step 1: Get IMDB metadata (corrects spelling too!)
    metadata = await get_imdb_metadata(client, query)
    if not metadata:
        print("[-] Could not get IMDB metadata. Aborting.")
        return None

    # Step 2: Fetch file using CORRECT title + year from IMDB
    title = metadata['title']
    year  = metadata['year']
    file_msg = await fetch_file_from_bots(client, title, year, is_series)
    if not file_msg:
        print("[-] Could not get file from any bot.")
        return None

    # Steps 3+4: Forward + Save
    return await forward_and_save(client, file_msg, metadata, db1_entity, mongo_db)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    mongo_db = None
    if MONGO_URI:
        mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        mongo_db = mongo_client["cinevault"]

    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        print("[*] Connected to Telegram!")
        # Test with a misspelled query — @imdbot will correct it!
        result = await smart_fetch(client, query="leo", is_series=False, mongo_db=mongo_db)
        if result:
            print(f"\n[[OK]] SUCCESS!\n{result}")
        else:
            print("\n[-] FAILED.")


if __name__ == "__main__":
    asyncio.run(main())
