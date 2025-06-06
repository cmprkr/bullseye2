import discord
import asyncio

# --- Discord Setup ---
DISCORD_TOKEN = ""
TARGET_CHANNEL_ID = 1379132047783624717

client = discord.Client(intents=discord.Intents.default())

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    channel = client.get_channel(TARGET_CHANNEL_ID)
    if channel:
        message = """

**Daily Trade Summary for 06/06/2025 @everyone**

Total Trades: 5 (4 Wins, 0 Losses, 1 Open Position)

Tier 1:
- SPY put @ $0.97. Sold at $1.07 2m later for a 10.31% gain :fire: :chart_with_upwards_trend:
- SPX call @ $0.95. Sold at $0.00 0m later for a 00.00% gain 

Tier 2:
- SPY call @ $1.00. Sold at $1.1 16m later for a 10.0% gain :fire: :chart_with_upwards_trend:
- SPY put @ $1.15. Sold at $1.25 16m later for a 8.7% gain :fire: :chart_with_upwards_trend:

Tier 3:
- DELL call @ $1.88. Sold at $1.96 1203m later for a 4.26% gain :fire: :chart_with_upwards_trend:
- TEM call @ $0.45. Sold at $0.15 117m later for a 66.67% loss

🔒 Want to see our open trades? [Get a premium membership](https://discord.com/channels/1350549258310385694/1372399067514011749)!
"""
        await channel.send(message)
        print("✅ Summary sent!")
    await client.close()

asyncio.run(client.start(DISCORD_TOKEN))
