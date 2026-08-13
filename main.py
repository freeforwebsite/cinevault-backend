import os
import asyncio
import motor.motor_asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest

# Your Telegram API credentials
api_id = 31654968
api_hash = 'b00f22e26a8c38db4172ce84f7d96ae2'
session_name = 'lyra_userbot_session'
HARDCODED_SESSION = "1BVtsOLkBu5JeC5sPJ_3ZAay5Xlhypv-6MSBYCjeaXb6PswozZwlkaoBJm1_xqFkkqsT5rznnbt0-0O79dRxM87wc2ZWWI8ZvsWGkcmteEgAWCyX_n7F4iESRiUA7lqpLHqawrxj8fR8GYs7Kkd4mwtrhTo_sFoyT5tUoACMuPWL9UTZc1QToIR1VYTR3Arbbw113nzorwflmVQIT0oDoZ-YjbAJEQxoCYp7JZrsq-iwVc0kdFgDw8a35CBzPnqTeLfjRl4lBV182IFtS_ne2LAT-pi8jDcKy7RdAsHjeFbXqAGofFbolkJxavHvX3aVgSAm8InBFysOzggO-nZie0smGZiD1_iw="
session_string = os.environ.get('TELEGRAM_SESSION_STRING', HARDCODED_SESSION)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to your Admin WebApp's URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB
MONGO_URI = "mongodb+srv://dharani2006lakshmi_db_user:Byi5WDiKV6H53Xe1@cluster0.p93dbly.mongodb.net"
mongo_db = None
if MONGO_URI:
    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    mongo_db = mongo_client["cinevault"]

@app.get("/")
@app.head("/")
async def root():
    return {"status": "alive", "message": "CineVault Harvester is running"}

@app.get("/status")
def read_status():
    return {"status": "running"}

@app.get("/search")
async def search_movies(q: str):
    if mongo_db is None:
        raise HTTPException(status_code=500, detail="Database not connected")
    
    collection = mongo_db["harvester_memory"]
    
    # Case-insensitive regex search on the title
    query = {"title": {"$regex": q, "$options": "i"}}
    
    results = []
    # Limit to top 20 results to avoid massive payload
    cursor = collection.find(query).limit(20)
    
    async for doc in cursor:
        results.append({
            "id": str(doc.get("_id", "")),
            "title": doc.get("title", "Unknown"),
            "message_id": doc.get("message_id", 0),
            "quality": doc.get("quality", "Unknown"),
            "file_size": doc.get("file_size", 0)
        })
        
    return {"results": results}

client = None

SEARCH_BOT_USERNAME = "@Cineplexmovreqbot"
STREAM_BOT_USERNAME = "@TG_FileStreamBot" # Converts Telegram files to direct stream links

@app.on_event("startup")
async def startup_event():
    global client
    print("[!] Connecting to Telegram Ghost Session...")
    
    if session_string:
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
    else:
        client = TelegramClient(session_name, api_id, api_hash)
        
    await client.connect()
    if not await client.is_user_authorized():
        print("[!] ERROR: Session invalid. You must run this locally to generate a new session.")
        return
    
    # Run the VT1 Harvester as a background task sharing this exact client!
    # from vt1_harvester import run_harvester
    # asyncio.create_task(run_harvester(client))
    
    print("[*] Successfully hooked into Telegram!")

@app.on_event("shutdown")
async def shutdown_event():
    await client.disconnect()

DB1_CHANNEL_ID = "https://t.me/+I9jiBz3SjvRlNjNl"

# ── New: List all movies from MongoDB ──────────────────────────────────────
@app.get("/movies")
async def list_movies(page: int = 1, limit: int = 20, language: str = None, quality: str = None):
    """Return paginated list of all movies saved in MongoDB."""
    if mongo_db is None:
        raise HTTPException(status_code=503, detail="MongoDB not connected")
    query = {}
    if language:
        query["language"] = {"$regex": language, "$options": "i"}
    if quality:
        query["quality"] = quality
    skip = (page - 1) * limit
    cursor = mongo_db.movies.find(query, {"_id": 0}).skip(skip).limit(limit).sort("timestamp", -1)
    movies = await cursor.to_list(length=limit)
    total = await mongo_db.movies.count_documents(query)
    return {"total": total, "page": page, "movies": movies}

# ── New: On-demand fetch a movie from bot ──────────────────────────────────
@app.post("/fetch")
async def fetch_movie_on_demand(request: Request):
    """
    On-demand: if a movie is not in the database, fetch it from the bot immediately.
    Body: { "title": "Master", "year": 2021, "tmdb_id": 723919, "poster": "...", "is_series": false }
    """
    if not client or not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Telegram session not authorized.")

    body = await request.json()
    title     = body.get("title", "")
    year      = body.get("year", 0)
    tmdb_id   = body.get("tmdb_id")
    poster    = body.get("poster")
    is_series = body.get("is_series", False)

    if not title or not year:
        raise HTTPException(status_code=400, detail="title and year are required")

    from bot_fetcher import smart_fetch
    # query = title + year if provided, else just title (imdbot will correct spelling)
    query = f"{title} {year}" if year else title
    result = await smart_fetch(
        client,
        query=query,
        is_series=is_series,
        mongo_db=mongo_db,
    )

    if result:
        return {"success": True, "data": result}
    else:
        raise HTTPException(status_code=404, detail=f"Could not fetch '{title} {year}' from any bot.")


from fastapi import Request
from fastapi.responses import StreamingResponse

active_downloads = {}

async def background_downloader(client, bot_entity, message_id, document, file_path):
    temp_path = file_path + ".tmp"
    try:
        with open(temp_path, "wb") as f:
            async for chunk in client.iter_download(document):
                f.write(chunk)
        import os
        try:
            os.rename(temp_path, file_path)
        except Exception:
            pass # Windows permission error if file is still open by a viewer, will remain as .tmp
    except Exception as e:
        print(f"[!] Background download failed for {message_id}: {e}")
        import os
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
    finally:
        if message_id in active_downloads:
            del active_downloads[message_id]

@app.get("/inventory")
async def get_inventory():
    """Returns all harvested movies for the Admin WebApp."""
    cursor = mongo_db.harvester_memory.find().sort("timestamp", -1)
    movies = await cursor.to_list(length=1000)
    
    formatted = []
    for m in movies:
        formatted.append({
            "id": str(m.get("tmdb_id", m.get("title", ""))),
            "title": m.get("title", "Unknown"),
            "poster": m.get("poster", ""),
            "quality": m.get("quality", "HD"),
            "language": m.get("language", "Unknown"),
            "messageId": str(m.get("message_id", "")),
            "tmdbId": m.get("tmdb_id"),
            "fileId": m.get("file_id", ""),
            "fileName": m.get("file_name", "")
        })
    return formatted

@app.get("/storefront")
async def get_storefront():
    """
    Returns the dynamic storefront configuration (list of rows) for the Android App.
    Reads from the 'storefront_config' MongoDB collection.
    """
    config = await mongo_db.storefront_config.find_one({"_id": "master_config"})
    if config and "rows" in config:
        return config
        
    # Default fallback if admin hasn't configured anything yet
    return {
        "banners": [],
        "rows": [
            { "id": "default1", "title": "Storefront Empty", "movies": [] }
        ]
    }

@app.post("/storefront")
async def save_storefront(request: Request):
    """
    Saves the storefront configuration (list of rows) from the Admin WebApp.
    """
    data = await request.json()
    await mongo_db.storefront_config.update_one(
        {"_id": "master_config"},
        {"$set": data},
        upsert=True
    )
    return {"status": "success", "message": "Storefront saved."}

@app.api_route("/stream/{bot_username}/{message_id}", methods=["GET", "HEAD"])
async def stream_telegram_file(request: Request, bot_username: str, message_id: int):
    try:
        target_peer = int(bot_username) if bot_username.lstrip('-').isdigit() else bot_username
        
        try:
            bot_entity = await client.get_entity(target_peer)
            msg_list = await client.get_messages(bot_entity, ids=[message_id])
        except Exception as e:
            import traceback
            print(f"[!] STREAM ENDPOINT ERROR: {repr(e)}")
            traceback.print_exc()
            if "disconnect" in str(e).lower() or not client.is_connected():
                print("[!] Telethon connection lost! Reconnecting...")
                await client.connect()
                bot_entity = await client.get_entity(target_peer)
                msg_list = await client.get_messages(bot_entity, ids=[message_id])
            else:
                raise HTTPException(status_code=500, detail=f"Error: {repr(e)}")
                
        msg = msg_list[0] if msg_list else None
        
        if not msg or not msg.document:
            raise HTTPException(status_code=404, detail="Movie not found")
            
        document = msg.document
        file_size = document.size
        
        # Cache Setup
        import os
        import asyncio
        cache_dir = "temp_cache"
        os.makedirs(cache_dir, exist_ok=True)
        file_path = os.path.join(cache_dir, f"{message_id}.mp4")
        temp_path = file_path + ".tmp"
        
        # Trigger background download if not already cached/downloading
        if not os.path.exists(file_path) and message_id not in active_downloads:
            active_downloads[message_id] = True
            asyncio.create_task(background_downloader(client, bot_entity, message_id, document, file_path))
        
        range_header = request.headers.get("Range", "bytes=0-")
        start_byte = 0
        end_byte = file_size - 1
        
        if range_header:
            range_str = range_header.replace("bytes=", "")
            parts = range_str.split("-")
            start_byte = int(parts[0]) if parts[0] else 0
            if len(parts) > 1 and parts[1]:
                end_byte = int(parts[1])
                
        async def file_generator():
            downloaded = 0
            length_to_download = end_byte - start_byte + 1
            target_path = file_path if os.path.exists(file_path) else temp_path
            
            # Step 1: Serve from local cache disk if the bytes are available
            if os.path.exists(target_path) and start_byte < os.path.getsize(target_path):
                with open(target_path, "rb") as f:
                    f.seek(start_byte)
                    while downloaded < length_to_download:
                        chunk_size = min(1024 * 1024, length_to_download - downloaded)
                        chunk = f.read(chunk_size)
                        
                        if chunk:
                            yield chunk
                            downloaded += len(chunk)
                        else:
                            # Reached EOF on disk
                            if message_id in active_downloads:
                                await asyncio.sleep(0.5) # Wait for downloader to fetch more
                                f.seek(f.tell()) # MUST reset EOF state in Python on Windows to read appended bytes!
                            else:
                                break # Downloader finished or crashed, fallback to Telegram stream
                                
            # Step 2: Fallback to direct Telegram stream for remaining bytes (like seeking to the end)
            if downloaded < length_to_download:
                remaining_offset = start_byte + downloaded
                async for chunk in client.iter_download(document, offset=remaining_offset):
                    if downloaded + len(chunk) > length_to_download:
                        yield bytes(chunk[:length_to_download - downloaded])
                        break
                    yield bytes(chunk)
                    downloaded += len(chunk)
                
        # Determine correct MIME type (ExoPlayer fails if we send video/mp4 for an MKV file)
        mime_type = document.mime_type or "application/octet-stream"
        
        # Check attributes for the real filename
        for attr in document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                if attr.file_name.lower().endswith(".mkv"):
                    mime_type = "video/x-matroska"
                elif attr.file_name.lower().endswith(".mp4"):
                    mime_type = "video/mp4"
                break
                
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
            "Content-Length": str(end_byte - start_byte + 1),
            "Content-Type": mime_type
        }
        
        return StreamingResponse(file_generator(), status_code=206, headers=headers, media_type=mime_type)
    except Exception as e:
        import traceback
        print(f"Stream ERROR REPR: {repr(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Outer Error: {repr(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8005, reload=False)
