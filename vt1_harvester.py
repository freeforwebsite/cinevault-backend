import os
import asyncio
import aiohttp
import json
import re
import sys
import io
import time
import google.generativeai as genai
from datetime import datetime
import motor.motor_asyncio
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import InputPeerChannel

def cprint(text):
    print(text, flush=True)

# --- Configuration ---
TMDB_API_KEY = "74683f7b34f7b689d84fcd8e0016d82a"
API_ID = 31654968
API_HASH = 'b00f22e26a8c38db4172ce84f7d96ae2'
HARDCODED_SESSION = "1BVtsOLkBu5JeC5sPJ_3ZAay5Xlhypv-6MSBYCjeaXb6PswozZwlkaoBJm1_xqFkkqsT5rznnbt0-0O79dRxM87wc2ZWWI8ZvsWGkcmteEgAWCyX_n7F4iESRiUA7lqpLHqawrxj8fR8GYs7Kkd4mwtrhTo_sFoyT5tUoACMuPWL9UTZc1QToIR1VYTR3Arbbw113nzorwflmVQIT0oDoZ-YjbAJEQxoCYp7JZrsq-iwVc0kdFgDw8a35CBzPnqTeLfjRl4lBV182IFtS_ne2LAT-pi8jDcKy7RdAsHjeFbXqAGofFbolkJxavHvX3aVgSAm8InBFysOzggO-nZie0smGZiD1_iw="
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING', HARDCODED_SESSION)
SESSION_STRING_2 = os.environ.get('TELEGRAM_SESSION_STRING_2', "")

# We will set this when the user creates the channel
DB1_CHANNEL_ID = -1004413411497

SEARCH_BOT = "@Cineplexmovreqbot"

# Setup Gemini API Key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 15 seconds delay for faster harvesting
DELAY_BETWEEN_MOVIES = 15 

# Daily Report Tracking
daily_success = []
daily_failed = []
daily_mismatched = []
daily_not_tamil = []

# Smart Resume State
processed_tmdb_ids = set()
attempted_join_links = set()

REPORT_BOT_TOKEN = os.environ.get("REPORT_BOT_TOKEN", "8727236866:AAFNtEftX_80eO1C3hzLq13O7nMWJISvgeM")
REQUEST_BOT_TOKEN = os.environ.get("REQUEST_BOT_TOKEN", "8888137591:AAHq-PiTZ0kR1k8tElTU8YcITKjCCMUbjDE")
# MongoDB Configuration
MONGO_URI = "mongodb+srv://dharani2006lakshmi_db_user:Byi5WDiKV6H53Xe1@cluster0.p93dbly.mongodb.net"
client_mongo = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client_mongo["cinevault"]
harvester_collection = db["harvester_memory"]

if not SESSION_STRING:
    cprint("[!] ERROR: TELEGRAM_SESSION_STRING environment variable not set.")
    exit(1)

client = None
bot_client = None
public_bot = None
mongo_db = None
db1_entity = None
dashboard_bridge_queue = asyncio.Queue()
public_bridge_queue = asyncio.Queue()

# Global Status Tracker for Debugging
current_status = "Booting up..."

def get_client():
    global client
    if client is None:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    return client

async def fetch_tmdb_movies(pages=10):
    """Fetches a mix of Tamil and global movies from TMDB."""
    movies = []
    async with aiohttp.ClientSession() as session:
        # 1. Fetch Popular TAMIL Movies
        for page in range(1, int(pages/2) + 1):
            url = f"https://api.tmdb.org/3/discover/movie?api_key={TMDB_API_KEY}&with_original_language=ta&sort_by=popularity.desc&page={page}"
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get('results', []):
                    if item.get('release_date'):
                        movies.append(item)
                        
        # 2. Fetch New TAMIL Movies (Recent releases)
        for page in range(1, int(pages/2) + 1):
            url = f"https://api.tmdb.org/3/discover/movie?api_key={TMDB_API_KEY}&with_original_language=ta&sort_by=primary_release_date.desc&page={page}"
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get('results', []):
                    if item.get('release_date') and item not in movies:
                        movies.append(item)
                        
        # 3. Fetch Global Popular (Hollywood etc)
        for page in range(1, int(pages/2) + 1):
            url = f"https://api.tmdb.org/3/movie/popular?api_key={TMDB_API_KEY}&page={page}"
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get('results', []):
                    if item.get('release_date') and item not in movies:
                        movies.append(item)
    
    # Remove duplicates based on ID
    unique_movies = {m['id']: m for m in movies}.values()
    return list(unique_movies)

async def process_quality_link(quality, q_url, movie, language):
    global daily_mismatched
    global mongo_db
    c = get_client()
    title = movie['title']
    year = movie['release_date'][:4]
    tmdb_id = movie['id']
    
    cprint(f"[*] Processing {quality} for {title}...")
    try:
        bot2_username = q_url.split('?start=')[0].split('/')[-1]
        start_param = q_url.split('?start=')[-1]
        
        bot2 = await c.get_entity(bot2_username)
        
        # Get the ID of the last message BEFORE we send our command
        last_msgs = await c.get_messages(bot2, limit=1)
        last_id = last_msgs[0].id if last_msgs else 0
        
        await c.send_message(bot2, f"/start {start_param}")
        
        # Wait and fetch only NEW messages that arrived after our command
        messages = []
        for _ in range(3):
            await asyncio.sleep(3)
            messages = await c.get_messages(bot2, limit=10, min_id=last_id)
            if messages:
                break
                
        # Check if the bot already gave us the Get File button directly!
        get_file_url = None
        join_links = []
        for msg in messages:
            if msg.reply_markup and hasattr(msg.reply_markup, 'rows'):
                for row in msg.reply_markup.rows:
                    for btn in row.buttons:
                        if hasattr(btn, 'url') and hasattr(btn, 'text'):
                            btn_text = btn.text.lower()
                            if 'get' in btn_text and 'file' in btn_text:
                                get_file_url = btn.url
                            elif 'join' in btn_text or 'channel' in btn_text or 'back-up' in btn_text:
                                join_links.append(btn.url)
                                
        # If we already have the get_file_url, we DO NOT need to join any channels!
        if join_links and not get_file_url:
            import telethon
            cprint(f"[*] Found {len(join_links)} Backup Channel Links.")
            global attempted_join_links
            joined_one = False
            
            for j_link in join_links:
                if j_link in attempted_join_links:
                    continue # Skip links we have already clicked in the past!
                    
                cprint(f"[*] Attempting to send join request: {j_link}")
                try:
                    if '+' in j_link:
                        invite_hash = j_link.split('+')[-1].split('?')[0].strip('/')
                        await c(ImportChatInviteRequest(invite_hash))
                    elif 'joinchat/' in j_link:
                        invite_hash = j_link.split('joinchat/')[-1].split('?')[0].strip('/')
                        await c(ImportChatInviteRequest(invite_hash))
                    elif 't.me/' in j_link:
                        channel_username = j_link.split('t.me/')[-1].split('/')[0].split('?')[0]
                        if channel_username.lower() != 'joinchat':
                            await c(JoinChannelRequest(channel_username))
                            
                    cprint(f"[+] Successfully clicked join link: {j_link}")
                    attempted_join_links.add(j_link)
                    joined_one = True
                except telethon.errors.FloodWaitError as e:
                    cprint(f"\n[!!!] TELEGRAM ANTI-SPAM TRIGGERED [!!!]")
                    cprint(f"Sleeping for {e.seconds} seconds to clear the block...")
                    await asyncio.sleep(e.seconds + 5)
                except Exception as e:
                    cprint(f"[-] Failed to join {j_link}: {e}")
                    attempted_join_links.add(j_link) # Add it anyway so we don't spam it
            
            if joined_one:
                cprint(f"[*] Waiting for bot to send file automatically after join...")     
                
                # Fetch NEW messages automatically (do NOT re-trigger /start!)
            
            # Fetch NEW messages again
            messages = []
            for _ in range(3):
                await asyncio.sleep(3)
                messages = await c.get_messages(bot2, limit=10, min_id=last_id)
                if messages:
                    break
                    
            # Check for get_file_url again in the new messages
            for msg in messages:
                if msg.reply_markup and hasattr(msg.reply_markup, 'rows'):
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, 'url') and hasattr(btn, 'text'):
                                btn_text = btn.text.lower()
                                if 'get' in btn_text and 'file' in btn_text:
                                    get_file_url = btn.url
                                
        if get_file_url:
            channel_id = None
            message_id = None
            
            if '/c/' in get_file_url:
                match = re.search(r'/c/(\d+)/(\d+)', get_file_url)
                if match:
                    channel_id = f"-100{match.group(1)}"
                    message_id = int(match.group(2))
            else:
                match = re.search(r't\.me/([^/]+)/(\d+)', get_file_url)
                if match:
                    channel_id = match.group(1)
                    message_id = int(match.group(2))
                    
            if channel_id and message_id:
                target_peer = int(channel_id) if channel_id.startswith('-100') else channel_id
                cprint(f"[*] Fetching media message {message_id} from {channel_id}...")
                
                # Fetch the actual media message from the channel
                import telethon
                try:
                    media_msg_list = await c.get_messages(target_peer, ids=[message_id])
                except telethon.errors.FloodWaitError as e:
                    cprint(f"[!!!] FloodWaitError fetching media! Sleeping {e.seconds}s...")
                    await asyncio.sleep(e.seconds)
                    return False
                except Exception as e:
                    cprint(f"[-] Error fetching media: {e}")
                    return False
                    
                if media_msg_list and media_msg_list[0] and media_msg_list[0].media:
                    media_msg = media_msg_list[0]
                    cprint(f"[+] Successfully fetched media message.")
                    
                    # Verify the actual file name matches the requested movie!
                    if hasattr(media_msg, 'file') and media_msg.file and media_msg.file.name:
                        file_name = media_msg.file.name.lower()
                        # Use math filter to check if the main title words are in the filename
                        tmdb_words = [w for w in re.sub(r'[^a-zA-Z0-9\s]', '', movie['title'].lower()).split() if len(w) > 2]
                        if tmdb_words:
                            is_correct = False
                            for word in tmdb_words:
                                if word in file_name:
                                    is_correct = True
                                    break
                                    
                            if not is_correct:
                                cprint(f"[-] FATAL MISMATCH: The file '{file_name}' does not match requested movie '{movie['title']}'! Rejecting...")
                                global daily_mismatched
                                daily_mismatched.append(f"{movie['title']} (File: {file_name})")
                                return False
                                
                    # Check if the original media filename contains 'tamil' or 'hin' etc
                    # We also explicitly tag it with the language detected during the button click.
                    caption = f"🎬 **{title} ({year})**\n\n💿 Quality: {quality}\n🗣 Language: {language}\n\n#TMDB_{tmdb_id}"
                    
                    # Forward to DB1!
                    global db1_entity
                    target = db1_entity if db1_entity else DB1_CHANNEL_ID
                    cprint(f"[*] Forwarding file to DB1...")
                    try:
                        sent_msg = await c.send_message(target, file=media_msg.media, message=caption)
                        file_size_gb = (media_msg.media.document.size / (1024 * 1024 * 1024)) if hasattr(media_msg.media, 'document') else 0
                        cprint(f"[+] Successfully saved {quality} ({language}) to DB1!")
                    except telethon.errors.FloodWaitError as e:
                        cprint(f"[!!!] FloodWaitError sending to DB1! Sleeping {e.seconds}s...")
                        await asyncio.sleep(e.seconds)
                        return False
                    except Exception as e:
                        cprint(f"[-] Error sending file to DB1: {e}")
                        return False
                    
                    if mongo_db is not None:
                        try:
                            # Save to harvester_memory so the /search endpoint can find it
                            await mongo_db.harvester_memory.update_one(
                                {"tmdb_id": movie['id'], "quality": quality},
                                {"$set": {
                                    "title": movie['title'],
                                    "language": language,
                                    "message_id": sent_msg.id,
                                    "file_size": file_size_gb,
                                    "timestamp": datetime.utcnow()
                                }},
                                upsert=True
                            )
                        except Exception as e:
                            cprint(f"[-] MongoDB upload save failed: {e}")
                            
                    return True
        cprint(f"[-] Failed to get final file for {quality}")
        return False
    except Exception as e:
        cprint(f"[-] Error processing {quality}: {e}")
        return False

async def hack_maze_for_movie(movie):
    """The core scraping logic from main.py, adapted for VT1."""
    global daily_success, daily_failed, daily_mismatched
    global mongo_db
    
    c = get_client()
    title = movie['title']
    year = movie['release_date'][:4]
    tmdb_id = movie['id']
    query = f"{title} {year}"
    
    cprint(f"\n[*] Processing Movie: {query} (ID: {tmdb_id})")
    
    try:
        search_bot = await c.get_entity(SEARCH_BOT)
        await c.send_message(search_bot, query)
        await asyncio.sleep(5) # Wait for bot response
        
        messages = await c.get_messages(search_bot, limit=5)
        
        # Verify that the bot actually returned the correct movie!
        bot_response_text = ""
        for msg in messages:
            if msg.text:
                bot_response_text += msg.text.lower() + " "
                
        is_correct_movie = None
        if GEMINI_API_KEY:
            try:
                # Dynamically fetch the current working models
                available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                # Prioritize the first 'flash' model
                flash_models = [m for m in available_models if 'flash' in m.lower()]
                model_name = flash_models[0] if flash_models else 'gemini-1.5-flash'
                
                model = genai.GenerativeModel(model_name)
                prompt = f"""
                I requested the movie "{title} ({year})" from a search bot.
                The bot replied with this text:
                "{bot_response_text}"
                
                Does the bot's reply represent the correct movie I asked for? 
                Reply ONLY with the word "YES" if it is the correct movie, or "NO" if it is completely the wrong movie (like returning 'Karuppu' when asked for 'Blast').
                """
                response = model.generate_content(prompt)
                if "YES" in response.text.upper():
                    is_correct_movie = True
                else:
                    is_correct_movie = False
                    
            except Exception as e:
                cprint(f"[-] Gemini API Error: Your key might be blocked or out of quota. Falling back to Math Filter instantly!")
                is_correct_movie = None
                
        if is_correct_movie is None:
            # Basic Fuzzy Match Fallback
            tmdb_words = [w for w in re.sub(r'[^a-zA-Z0-9\s]', '', title.lower()).split() if len(w) > 2]
            is_correct_movie = False
            if not tmdb_words:
                is_correct_movie = True # Fallback if title is just numbers or weird chars
            else:
                for word in tmdb_words:
                    if word in bot_response_text:
                        is_correct_movie = True
                        break
                        
        if not is_correct_movie:
            cprint(f"[-] AI REJECTED: Bot returned a different movie instead of '{title}'!")
            daily_mismatched.append(f"{title} (Bot text confused)")
            if mongo_db is not None:
                try:
                    await mongo_db.failed_movies.insert_one({"tmdb_id": tmdb_id, "title": title, "reason": "AI Rejected", "timestamp": datetime.utcnow()})
                except Exception:
                    pass
            return

        quality_links = {}
        found_tamil = False
        
        for msg in messages:
            if msg.reply_markup and hasattr(msg.reply_markup, 'rows'):
                
                # First pass: Look for Tamil
                for row in msg.reply_markup.rows:
                    for btn in row.buttons:
                        if hasattr(btn, 'url') and hasattr(btn, 'text'):
                            text = btn.text.lower()
                            if 'tamil' in text:
                                found_tamil = True
                                if '1080' in text: quality_links['1080p'] = btn.url
                                elif '720' in text: quality_links['720p'] = btn.url
                                elif '360' in text or '480' in text: quality_links['360p/480p'] = btn.url
                                
                # Second pass: If no Tamil found, fallback to English or Any
                if not quality_links:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if hasattr(btn, 'url') and hasattr(btn, 'text'):
                                text = btn.text.lower()
                                if '1080' in text: quality_links['1080p'] = btn.url
                                elif '720' in text: quality_links['720p'] = btn.url
                                elif '360' in text or '480' in text: quality_links['360p/480p'] = btn.url
        
        if not quality_links:
            cprint(f"[-] No qualities found for {query}")
            daily_failed.append(f"{title} (No Links Found)")
            if mongo_db is not None:
                try:
                    await mongo_db.failed_movies.insert_one({"tmdb_id": tmdb_id, "title": title, "reason": "No Links Found", "timestamp": datetime.utcnow()})
                except Exception:
                    pass
            return
            
        language = "Tamil" if found_tamil else "English"
        
        # Process each quality sequentially
        for quality, url in quality_links.items():
            await process_quality_link(quality, url, movie, language)
            cprint(f"[*] Sleeping 15 seconds before fetching the next quality...")
            await asyncio.sleep(15) # 15 second pause between qualities to prevent spam!
        
        daily_success.append(f"{title} ({language})")
        if language != "Tamil":
            daily_not_tamil.append(f"{title} ({language})")

    except Exception as e:
        cprint(f"[-] Failed to process {query}: {e}")
        daily_failed.append(f"{title} (Error: {e})")
        if mongo_db is not None:
            try:
                await mongo_db.failed_movies.insert_one({"tmdb_id": tmdb_id, "title": title, "reason": f"Error: {e}", "timestamp": datetime.utcnow()})
            except Exception:
                pass

async def run_harvester(external_client=None):
    if not DB1_CHANNEL_ID:
        cprint("[!] ERROR: DB1_CHANNEL_ID is not set! Please create the channel and update the script.")
        return

    global client
    if external_client:
        client = external_client
    else:
        client = get_client()
        await client.connect()
        
    if not await client.is_user_authorized():
        cprint("[!] ERROR: Telegram session invalid.")
        return
        
    global processed_tmdb_ids
    global mongo_db
    global db1_entity
    
    # Reliably resolve DB1 entity
    try:
        cprint("[*] Resolving DB1 Channel Entity...")
        async for msg in client.iter_messages(DB1_CHANNEL_ID, limit=1):
            db1_entity = msg.chat
            break
        if db1_entity:
            cprint(f"[+] Successfully bound to DB1: {db1_entity.title}")
    except Exception as e:
        cprint(f"[-] Failed to resolve DB1 Entity: {e}")
        
    if MONGO_URI:
        try:
            cprint("[*] Connecting to MongoDB...")
            db_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
            mongo_db = db_client.cinevault
            
            cprint("[*] Loading Harvester memory from MongoDB...")
            
            # Load uploaded movies
            uploaded_cursor = mongo_db.uploaded_movies.find({}, {"tmdb_id": 1})
            async for doc in uploaded_cursor:
                processed_tmdb_ids.add(str(doc["tmdb_id"]))
                
            # Load failed movies
            failed_cursor = mongo_db.failed_movies.find({}, {"tmdb_id": 1})
            async for doc in failed_cursor:
                processed_tmdb_ids.add(str(doc["tmdb_id"]))
                
            cprint(f"[+] Smart Resume: Loaded {len(processed_tmdb_ids)} movies from MongoDB memory!")
        except Exception as e:
            cprint(f"[-] MongoDB connection failed: {e}. Falling back to DB1 scan!")
            mongo_db = None
            
    if mongo_db is None:
        cprint("[*] Scanning DB1 to learn which movies are already uploaded...")
        try:
            async for msg in client.iter_messages(DB1_CHANNEL_ID, limit=5000):
                if msg.text and '#TMDB_' in msg.text:
                    match = re.search(r'#TMDB_(\d+)', msg.text)
                    if match:
                        processed_tmdb_ids.add(match.group(1))
            cprint(f"[+] Smart Resume: Memorized {len(processed_tmdb_ids)} already-uploaded movies!")
        except Exception as e:
            cprint(f"[-] Failed to scan DB1: {e}")

    # Launch Bot Dashboard
    global bot_client
    if REPORT_BOT_TOKEN:
        try:
            bot_client = TelegramClient('report_bot_session', API_ID, API_HASH)
            await bot_client.start(bot_token=REPORT_BOT_TOKEN)
            
            me = await client.get_me()
            owner_id = me.id
            
            # Resolve DB1_CHANNEL_ID to a real numeric ID using the Userbot
            try:
                db1_entity = await client.get_entity(DB1_CHANNEL_ID)
                # Ensure it's formatted as a Telegram Channel ID (starts with -100)
                if str(db1_entity.id).startswith('-100'):
                    real_db1_id = int(db1_entity.id)
                else:
                    real_db1_id = int(f"-100{db1_entity.id}")
                    
                # Create an InputPeerChannel for the bots to use safely
                real_db1_input_entity = InputPeerChannel(db1_entity.id, db1_entity.access_hash)
            except Exception as e:
                cprint(f"[-] Failed to resolve DB1 ID. Please make sure the invite link is valid: {e}")
                real_db1_id = DB1_CHANNEL_ID
                real_db1_input_entity = DB1_CHANNEL_ID
            
            keyboard = [
                [Button.text("📊 Total Movies"), Button.text("✅ Uploaded Today")],
                [Button.text("❌ Mismatched Files"), Button.text("🌐 Not In Tamil")]
            ]
            
            # --- DASHBOARD BOT BRIDGE HANDLER ---
            @bot_client.on(events.NewMessage(from_users=owner_id))
            async def dashboard_bridge_handler(event):
                # Catch incoming files from the Userbot
                if event.media:
                    await dashboard_bridge_queue.put(event.message)
            
            @bot_client.on(events.NewMessage(func=lambda e: e.is_private and e.sender_id != owner_id))
            async def handle_commands(event):
                text = event.text
                cprint(f"[*] Dashboard Bot received message from {event.sender_id}: {text}")
                
                # --- DASHBOARD LOGIC (For Admin Only) ---
                if event.sender_id != owner_id:
                    # If they use the dual-purpose bot, still serve them public requests
                    if not text or text.startswith('/'):
                        welcome_text = (
                            "🎬 **Welcome to CineVault Request Bot!** 🍿\n\n"
                            "To download a movie, simply send me the name of the movie you want to watch.\n\n"
                            "📌 **Search Format Examples:**\n"
                            "✅ `Leo`\n"
                            "✅ `Avatar The Way of Water`\n"
                            "✅ `Leo 2023` (Add the year for better accuracy!)\n\n"
                            "Send your movie name below to start searching! 👇"
                        )
                        await event.reply(welcome_text)
                        return
                        
                    status_msg = await event.reply(f"🔍 Searching database for '{text}'...")
                    found = False
                    try:
                        # SECURITY BYPASS: Use the Userbot to search (because Bots are blocked from searching)
                        target = db1_entity if db1_entity else DB1_CHANNEL_ID
                        async for msg in client.iter_messages(target, search=text, limit=3):
                            if msg.media:
                                found = True
                                # SECURITY BYPASS 3: The Live Queue Bridge
                                # Since bots cannot use GetHistoryRequest, we catch the forwarded message in real-time!
                                bot_entity = await bot_client.get_me()
                                await client.forward_messages(bot_entity.username, msg)
                                
                                # Wait for the bridge handler to catch the file (timeout 5 seconds)
                                bot_msg = await asyncio.wait_for(dashboard_bridge_queue.get(), timeout=5.0)
                                await bot_client.send_message(event.sender_id, message=bot_msg.text, file=bot_msg.media)
                    except Exception as ex:
                        cprint(f"[-] Dashboard Bot search error: {ex}")
                        
                    if not found:
                        await status_msg.edit("❌ Sorry, this movie is not in our database yet!")
                    else:
                        await status_msg.edit("✅ Here are the files we found!")
                    return

                if text == "📊 Total Movies":
                    try:
                        # Use the main client (Userbot) to get the total because it owns the channel!
                        target = db1_entity if db1_entity else DB1_CHANNEL_ID
                        total = (await client.get_messages(target, limit=0)).total
                        await event.reply(f"🎬 **Total Movies in DB1:** {total}", buttons=keyboard)
                    except Exception as e:
                        await event.reply(f"❌ Error getting total: {e}")
                elif text == "✅ Uploaded Today":
                    log = f"✅ **Uploaded Today: {len(daily_success)}**\n\n" + "\n".join(daily_success)
                    for i in range(0, len(log), 4000): await event.reply(log[i:i+4000], buttons=keyboard)
                elif text == "❌ Mismatched Files":
                    log = f"❌ **Mismatched Files: {len(daily_mismatched)}**\n\n" + "\n".join(daily_mismatched)
                    for i in range(0, len(log), 4000): await event.reply(log[i:i+4000], buttons=keyboard)
                elif text == "🌐 Not In Tamil":
                    log = f"🌐 **Uploaded but Not In Tamil: {len(daily_not_tamil)}**\n\n" + "\n".join(daily_not_tamil)
                    for i in range(0, len(log), 4000): await event.reply(log[i:i+4000], buttons=keyboard)
                else:
                    await event.reply("Welcome to your CineVault Dashboard! Click a button below:", buttons=keyboard)
                    
            await bot_client.send_message(owner_id, "🚀 **Harvester Dashboard Online!**\nUse the buttons below to check live stats:", buttons=keyboard)
            cprint("[+] Successfully sent interactive dashboard to your Telegram!")
        except Exception as e:
            cprint(f"[-] Bot Dashboard Error: {e}")

    # Launch Secondary Public Request Bot (if provided)
    global public_bot
    if REQUEST_BOT_TOKEN:
        try:
            public_bot = TelegramClient('public_bot_session', API_ID, API_HASH)
            await public_bot.start(bot_token=REQUEST_BOT_TOKEN)
            
            # --- PUBLIC BOT BRIDGE HANDLER ---
            @public_bot.on(events.NewMessage(from_users=owner_id))
            async def public_bridge_handler(event):
                # Catch incoming files from the Userbot
                if event.media:
                    await public_bridge_queue.put(event.message)
                    
            # Use the same real_db1_id
            @public_bot.on(events.NewMessage(func=lambda e: e.is_private and e.sender_id != owner_id))
            async def handle_public_requests(event):
                text = event.text
                if not text or text.startswith('/'):
                    welcome_text = (
                        "🎬 **Welcome to CineVault!** 🍿\n\n"
                        "To download a movie, simply send me the name of the movie you want to watch.\n\n"
                        "📌 **Search Format Examples:**\n"
                        "✅ `Leo`\n"
                        "✅ `Avatar The Way of Water`\n"
                        "✅ `Leo 2023` (Add the year for better accuracy!)\n\n"
                        "Send your movie name below to start searching! 👇"
                    )
                    await event.reply(welcome_text)
                    return
                    
                status_msg = await event.reply(f"🔍 Searching database for '{text}'...")
                found = False
                try:
                    # SECURITY BYPASS: Use the Userbot to search (because Bots are blocked from searching)
                    target = db1_entity if db1_entity else DB1_CHANNEL_ID
                    async for msg in client.iter_messages(target, search=text, limit=3):
                        if msg.media:
                            found = True
                            # SECURITY BYPASS 3: The Live Queue Bridge
                            # Since bots cannot use GetHistoryRequest, we catch the forwarded message in real-time!
                            bot_entity = await public_bot.get_me()
                            await client.forward_messages(bot_entity.username, msg)
                            
                            # Wait for the bridge handler to catch the file (timeout 5 seconds)
                            bot_msg = await asyncio.wait_for(public_bridge_queue.get(), timeout=5.0)
                            await public_bot.send_message(event.sender_id, message=bot_msg.text, file=bot_msg.media)
                except Exception as ex:
                    cprint(f"[-] Public Bot search error: {ex}")
                    
                if not found:
                    await status_msg.edit("❌ Sorry, this movie is not in our database yet!")
                else:
                    await status_msg.edit("✅ Here are the files we found!")
            
            cprint("[+] Successfully booted the Dedicated Public Request Bot!")
            try:
                await public_bot.send_message(owner_id, "✅ **Public Request Bot is ONLINE!**\nI am connected to DB1 and ready to serve movies to your users!")
            except Exception:
                pass
        except Exception as e:
            cprint(f"[-] Public Request Bot Error: {e}")

    cprint("[+] VT1 Harvester Started. Fetching movies from TMDB...")
    
    global current_status
    current_status = "Starting Harvester Loop..."
    
    # Dual-Account Setup
    active_account = 1
    last_rotation_time = time.time()
    
    # We run this in an infinite loop for Render deployment!
    while True:
        try:
            # Check for Account Rotation (5 hours = 18000 seconds)
            if SESSION_STRING_2 and (time.time() - last_rotation_time > 18000):
                cprint("\n[!] 5 HOURS PASSED! ROTATING ACCOUNTS TO PREVENT SPAM BAN...")
                current_status = "Rotating Accounts..."
                
                # Disconnect current client safely
                try:
                    await client.disconnect()
                except Exception:
                    pass
                    
                # Swap Session String
                if active_account == 1:
                    new_session = SESSION_STRING_2
                    active_account = 2
                else:
                    new_session = SESSION_STRING
                    active_account = 1
                    
                # Reconnect
                client = TelegramClient(StringSession(new_session), API_ID, API_HASH)
                await client.connect()
                cprint(f"[+] Successfully hot-swapped to Account {active_account}!")
                last_rotation_time = time.time()
                
            current_status = "Fetching TMDB movies..."
            movies = await fetch_tmdb_movies(pages=50) # Fetch movies
            if not movies:
                current_status = "Failed to fetch TMDB movies, retrying..."
                cprint("[-] Could not fetch TMDB movies. Retrying in 60s...")
                await asyncio.sleep(60)
                continue
                
            cprint(f"[+] Found {len(movies)} movies to process.")
            
            for i, movie in enumerate(movies):
                if str(movie['id']) in processed_tmdb_ids:
                    continue # SKIP ALREADY UPLOADED!
                    
                current_status = f"Processing Movie {i+1}/{len(movies)}: {movie.get('title', 'Unknown')} (ID: {movie['id']})"
                cprint(f"\n--- Movie {i+1}/{len(movies)} ---")
                await hack_maze_for_movie(movie)
                processed_tmdb_ids.add(str(movie['id'])) # Mark as processed

                current_status = f"Sleeping for {DELAY_BETWEEN_MOVIES} seconds..."
                cprint(f"[*] Sleeping for {DELAY_BETWEEN_MOVIES} seconds to avoid rate limits...")
                await asyncio.sleep(DELAY_BETWEEN_MOVIES)
                
        except Exception as e:
            current_status = f"Harvester error: {e}. Retrying in 60s..."
            cprint(f"[!] Harvester encountered an error: {e}. Retrying in 60 seconds...")
            await asyncio.sleep(60)

async def daily_report_task(client):
    """Sends a daily report of uploaded movies to Saved Messages."""
    global daily_success, daily_failed
    # Set to 120 seconds (2 minutes) for testing! We will change to 24*60*60 after verification.
    REPORT_INTERVAL = 120 
    
    while True:
        await asyncio.sleep(REPORT_INTERVAL)
        
        if not daily_success and not daily_failed:
            continue
            
        cprint("[*] Generating Daily Report...")
        report_text = f"📊 **Daily Harvester Report**\n\n✅ Uploaded: {len(daily_success)}\n❌ Failed: {len(daily_failed)}\n\n"
        
        full_log = report_text + "=== ✅ SUCCESSFULLY UPLOADED ===\n" + "\n".join(daily_success) + "\n\n=== ❌ FAILED/REJECTED ===\n" + "\n".join(daily_failed)
        
        # Send to "me" (Saved Messages) as normal text (chunked if too long)
        try:
            # Telegram character limit is 4096. We'll chunk safely at 4000.
            chunk_size = 4000
            for i in range(0, len(full_log), chunk_size):
                await client.send_message("me", full_log[i:i+chunk_size])
                
            cprint("[+] Successfully sent Daily Report to Saved Messages!")
        except Exception as e:
            cprint(f"[-] Failed to send daily report: {e}")
            
        # Clear the lists for the next day
        daily_success.clear()
        daily_failed.clear()

if __name__ == "__main__":
    asyncio.run(run_harvester())
