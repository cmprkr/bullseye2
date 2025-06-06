

import discord
import asyncio
from datetime import datetime
import os

DISCORD_TOKEN = ""
CHANNEL_IDS = [1350549419204018176, 1371236886101754046, 1371194846353817832, 1371195227859320852]

async def _dump_all_channels():
    # Create a fresh client for each run
    intents = discord.Intents.default()
    intents.message_content = True
    temp_client = discord.Client(intents=intents)

    @temp_client.event
    async def on_ready():
        print(f"✅ Logged in as {temp_client.user}")
        local_tz = datetime.now().astimezone().tzinfo

        file_name = "full_channel_dump.txt"
        if os.path.exists(file_name):
            os.remove(file_name)

        with open(file_name, "w", encoding="utf-8") as f:
            for channel_id in CHANNEL_IDS:
                channel = temp_client.get_channel(channel_id)
                if not channel:
                    print(f"⚠️ Could not access channel {channel_id}")
                    continue

                messages = []
                async for msg in channel.history(limit=None):
                    messages.append(msg)

                print(f"📥 Found {len(messages)} messages in {channel.name}")

                for m in reversed(messages):
                    timestamp = m.created_at.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")
                    author = m.author.name
                    content = m.content
                    f.write(f"{channel.name} [{timestamp}] {author}: {content}\n")

        print("✅ Dumped all messages to full_channel_dump.txt")

        # After dumping, close just this temporary client
        await temp_client.close()

    # Start and wait for on_ready → dump → close
    await temp_client.start(DISCORD_TOKEN)

async def start_parser_bot():
    """
    Creates a new Discord client, dumps messages, then closes it.
    Every call to this function spins up a fresh client instance.
    """
    await _dump_all_channels()
