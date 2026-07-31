import os
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient

# --- Configuration ---
API_ID = 31654968
API_HASH = 'b00f22e26a8c38db4172ce84f7d96ae2'
HARDCODED_SESSION = "1BVtsOHABu3BB7QhhYVF-WdlB5eL-qj7owxLnGKRfhDyu9cCJOH5G28RVT2nTZmYZ9NnKNf68gQhAeGz5dv-EK4GPNvLohapqX8fykioSZckEc21NMfk5RmAxQMvfNlgy9BJvTz6pxJ088BivfmJ4i02F2_bj9cmLXfTUpG4aHUR-yNRrnJJZvl_iD4yBm4GOLwonxxn7K9QgS8j9jDvUUKmZyQdPtc_rRkPn03GUF-QwIrsVRuxy2f87-N69QMu9L0BAm2EK0gxOCANVnMrqwJfMdJaBX31SvI-YLTEO-iH2diOul1vIf3k2bs7a_XrxkkWfaVQLpmpcLBrZUotw5SR9P7NzNZ8="
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING', HARDCODED_SESSION)

DB1_CHANNEL_ID = "https://t.me/+I9jiBz3SjvRlNjNl"

if not SESSION_STRING:
    print("[!] ERROR: TELEGRAM_SESSION_STRING environment variable not set.")
    exit(1)

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) if SESSION_STRING else None
# Wait, we need to import StringSession
from telethon.sessions import StringSession
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

async def main():
    await client.connect()
    if not await client.is_user_authorized():
        print("[!] ERROR: Telegram session invalid.")
        return
        
    print("[+] VT2 Analyst Started. Scanning DB1...")
    
    try:
        db1 = await client.get_entity(DB1_CHANNEL_ID)
    except Exception as e:
        print(f"[!] Could not access DB1: {e}")
        return

    # Calculate time 24 hours ago (aware datetime)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    
    total_movies = 0
    qualities = {'1080p': 0, '720p': 0, '360p/480p': 0, 'Other': 0}
    total_bytes = 0
    
    # We iterate messages in reverse (newest first) until we hit 24h ago
    async for msg in client.iter_messages(db1):
        if msg.date < yesterday:
            break
            
        if msg.media and msg.message:
            total_movies += 1
            text = msg.message.lower()
            
            # Tally quality
            if '1080p' in text: qualities['1080p'] += 1
            elif '720p' in text: qualities['720p'] += 1
            elif '360p' in text or '480p' in text: qualities['360p/480p'] += 1
            else: qualities['Other'] += 1
                
            # Tally size
            if hasattr(msg.media, 'document'):
                total_bytes += msg.media.document.size
                
    # Convert bytes to GB
    total_gb = total_bytes / (1024**3)
    
    # Generate Report
    report = (
        f"📊 **VT2 Daily Database Report**\n"
        f"📅 Date: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"🎬 **Total Files Uploaded:** {total_movies}\n"
        f"💾 **Total Storage Added:** {total_gb:.2f} GB\n\n"
        f"💿 **Quality Breakdown:**\n"
        f"  • 1080p: {qualities['1080p']}\n"
        f"  • 720p: {qualities['720p']}\n"
        f"  • 360p/480p: {qualities['360p/480p']}\n"
        f"  • Other: {qualities['Other']}\n\n"
        f"✅ Database is healthy and growing!"
    )
    
    print(report)
    
    # Send report to Saved Messages
    await client.send_message('me', report)
    print("[+] Report sent to Saved Messages!")

if __name__ == "__main__":
    asyncio.run(main())
