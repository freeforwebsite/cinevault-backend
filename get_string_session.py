from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import asyncio
import os

api_id = 31654968
api_hash = 'b00f22e26a8c38db4172ce84f7d96ae2'

async def main():
    print("[*] Generating a BRAND NEW String Session...")
    # Create a brand new StringSession from scratch
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()
    
    string_session = client.session.save()
    print("\n\n[+] Successfully generated new String Session!")
    print("\n======================================================\n")
    print(string_session)
    print("\n======================================================\n")
    
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    env_file = os.path.join(desktop_path, ".env")
    
    with open(env_file, "a") as f:
        f.write(f"\nTELEGRAM_SESSION_STRING={string_session}\n")
        
    print(f"[*] I also appended your new TELEGRAM_SESSION_STRING to: {env_file}")

if __name__ == '__main__':
    # Use the synchronous start method for interactive CLI input
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        string_session = client.session.save()
        print("\n\n[+] Successfully generated new String Session!")
        print("\n======================================================\n")
        print(string_session)
        print("\n======================================================\n")
        
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        env_file = os.path.join(desktop_path, ".env.txt")
        with open(env_file, "w") as f:
            f.write(f"TELEGRAM_SESSION_STRING={string_session}\n")
        print(f"[*] Saved to {env_file}")
