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

app = FastAPI()

# MongoDB
MONGO_URI = "mongodb+srv://freeforwebsitein_db_user:PQWHxVtcA1NnOqVO@cluster0.knj55zu.mongodb.net"
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
    import vt1_harvester
    return {"status": "running", "current_task": vt1_harvester.current_status}

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
    from vt1_harvester import run_harvester
    asyncio.create_task(run_harvester(client))
    
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

@app.get("/search")
async def search_movie(request: Request, query: str):
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Telegram session not authorized.")
    
    try:
        print(f"[*] Native DB1 Search for: {query}")
        db1 = await client.get_entity(DB1_CHANNEL_ID)
        
        streams = []
        render_url = str(request.base_url).rstrip("/")
        
        # Search the database channel
        async for msg in client.iter_messages(db1, search=query, limit=10):
            if msg.media and hasattr(msg.media, 'document') and msg.text:
                text = msg.text.lower()
                
                # Extract quality from the caption (e.g., 💿 Quality: 1080p)
                quality = "Unknown"
                if "1080p" in text: quality = "1080p"
                elif "720p" in text: quality = "720p"
                elif "480p" in text or "360p" in text: quality = "480p"
                
                # We assume VT1 prioritized Tamil, or fallback to English
                language = "TAMIL" if "tamil" in text else "ENGLISH"
                
                file_size_gb = msg.media.document.size / (1024 * 1024 * 1024)
                
                # The stream peer is DB1, and message ID is the message in DB1
                stream_peer = db1.id
                stream_msg_id = msg.id
                
                final_stream_url = f"{render_url}/stream/{stream_peer}/{stream_msg_id}"
                
                streams.append({
                    "quality": quality,
                    "language": language,
                    "size": f"{file_size_gb:.2f} GB",
                    "url": final_stream_url,
                    "rawText": "Native DB1 Stream"
                })
        
        if not streams:
            print(f"[-] No results found in DB1 for {query}")
            return JSONResponse({"streams": []})
            
        print(f"[*] Found {len(streams)} streams in DB1!")
        return JSONResponse({"streams": streams})
        
    except Exception as e:
         print(f"ERROR: {str(e)}")
         raise HTTPException(status_code=500, detail=str(e))

from fastapi import Request
from fastapi.responses import StreamingResponse

@app.get("/stream/{bot_username}/{message_id}")
async def stream_telegram_file(request: Request, bot_username: str, message_id: int):
    try:
        target_peer = int(bot_username) if bot_username.lstrip('-').isdigit() else bot_username
        bot_entity = await client.get_entity(target_peer)
        msg_list = await client.get_messages(bot_entity, ids=[message_id])
        msg = msg_list[0] if msg_list else None
        
        if not msg or not msg.document:
            raise HTTPException(status_code=404, detail="Movie not found")
            
        document = msg.document
        file_size = document.size
        
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
            async for chunk in client.iter_download(document, offset=start_byte):
                if downloaded + len(chunk) > length_to_download:
                    yield bytes(chunk[:length_to_download - downloaded])
                    break
                yield bytes(chunk)
                downloaded += len(chunk)
                
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
            "Content-Length": str(end_byte - start_byte + 1),
            "Content-Type": "video/mp4"
        }
        
        return StreamingResponse(file_generator(), status_code=206, headers=headers, media_type="video/mp4")
    except Exception as e:
        print(f"Stream ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
