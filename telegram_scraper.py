import os
import re
import asyncio
from fastapi import FastAPI, HTTPException
from telethon import TelegramClient, events
from telethon.tl.types import ReplyInlineMarkup

# =====================================================================
# CONFIGURATION
# =====================================================================
# Replace these with your actual Telegram API credentials from my.telegram.org
API_ID = 31654968
API_HASH = 'b00f22e26a8c38db4172ce84f7d96ae2'
SESSION_NAME = 'lyra_userbot_session'

# The username of the Telegram Movie Request Bot you want to scrape
TARGET_BOT_USERNAME = '@CineplexMovieBot' # Replace with actual bot username

# A free public stream converter API (or your own TG-Stream-Bot domain)
STREAM_CONVERTER_BASE_URL = 'https://your-stream-server.com/stream/'

app = FastAPI(title="Lyra Telegram Scraper API")

# Initialize the Telethon Client
# This creates a SQLite session file locally so you only have to log in once.
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

@app.on_event("startup")
async def startup_event():
    print("\n[!] Connecting to Telegram...")
    # client.start() will automatically prompt for phone number and code in the terminal
    # if the session file does not exist.
    await client.start()
    print("[*] Successfully logged in to Telegram!")

@app.get("/search")
async def search_movie(query: str):
    """
    Endpoint for the Flutter app. 
    Usage: http://localhost:8000/search?query=Bigil
    """
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter is required")
        
    try:
        # 1. Send the search command to the target Telegram bot
        # e.g., typing "/search Bigil" in the chat
        await client.send_message(TARGET_BOT_USERNAME, f"/search {query}")
        
        # 2. Wait for the bot's response
        # We wait up to 10 seconds for a new message in that chat
        response_event = await client.wait_for(
            events.NewMessage(chats=TARGET_BOT_USERNAME), 
            timeout=10.0
        )
        
        message = response_event.message
        
        # 3. Parse the Inline Keyboard Buttons
        if not message.reply_markup or not isinstance(message.reply_markup, ReplyInlineMarkup):
            raise HTTPException(status_code=404, detail="No buttons found in bot response")

        streams = []
        
        # Loop through every row of buttons attached to the message
        for row in message.reply_markup.rows:
            for button in row.buttons:
                button_text = button.text.lower()
                
                # Regex to extract file size (e.g., "3.83 GB", "782 MB")
                size_match = re.search(r'(\d+(?:\.\d+)?\s*(?:GB|MB))', button.text, re.IGNORECASE)
                file_size = size_match.group(1) if size_match else "Unknown Size"
                
                # Determine Quality
                quality = "Unknown"
                if "1080p" in button_text or "1080" in button_text:
                    quality = "1080p"
                elif "720p" in button_text or "720" in button_text:
                    quality = "720p"
                elif "480p" in button_text or "480" in button_text:
                    quality = "480p"
                elif "4k" in button_text:
                    quality = "4K"
                    
                # Determine Language Track (if specified)
                language = "Tamil" # Defaulting for this app
                if "english" in button_text:
                    language = "English"
                elif "multi" in button_text:
                    language = "Multi-Audio"
                
                # We extract the callback data or URL from the button.
                # In typical Telegram bots, clicking the button returns a hidden file ID 
                # or triggers a download payload. Here, we mock the extraction process
                # assuming the bot uses deep-linking or callback payloads.
                extracted_file_id = "mock_file_12345"
                
                # Convert the Telegram file ID to a direct HTTP stream URL 
                # using your Stream Converter server
                stream_url = f"{STREAM_CONVERTER_BASE_URL}{extracted_file_id}.mp4"
                
                streams.append({
                    "quality": quality,
                    "language": language,
                    "size": file_size,
                    "url": stream_url,
                    "raw_text": button.text
                })
        
        # Return the clean JSON array to the Flutter app
        return {
            "movie": query,
            "streams": streams
        }

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="The Telegram bot did not respond in time.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8080))
    # Run the server on port 8080 for Replit compatibility
    uvicorn.run(app, host="0.0.0.0", port=port)
