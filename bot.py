import discord
import asyncio
from openai import OpenAI
from datetime import datetime, timedelta
import pytz
import re
from analytics import run_trade_summary
from parse_signals import start_parser_bot

DISCORD_TOKEN = ""
OPENAI_KEY = ""
CHANNEL_ID_TRIGGER = 1379132047783624717
CHANNEL_ID_SECONDARY_OUTPUT = 1379132006629118113

client = discord.Client(intents=discord.Intents.all())
openai_client = OpenAI(api_key=OPENAI_KEY)

last_summary_message = ""

async def schedule_push(target_time, message, output_channel):
    """Schedule sending last_summary_message to output_channel at target_time (EST)."""
    est = pytz.timezone("US/Eastern")
    now = datetime.now(est)
    
    # Combine today's date with target_time
    target_dt = est.localize(datetime.combine(now.date(), target_time))
    
    # If target time has passed, schedule for tomorrow
    if target_dt <= now:
        target_dt += timedelta(days=1)
    
    # Calculate delay in seconds
    delay = (target_dt - now).total_seconds()
    
    # Send confirmation
    await message.channel.send(f"✅ Push scheduled for {target_dt.strftime('%I:%M %p')} EST on {target_dt.strftime('%m/%d/%Y')}.")
    
    # Wait until target time
    await asyncio.sleep(delay)
    
    # Check if message and channel are still valid
    if not last_summary_message:
        await message.channel.send("⚠️ No message available to push at scheduled time.")
        return
    
    if not output_channel:
        await message.channel.send("❌ Could not find the output channel at scheduled time.")
        return
    
    # Send the message
    await output_channel.send(last_summary_message)
    await message.channel.send(f"✅ Scheduled message posted in **{output_channel.name}** at {target_dt.strftime('%I:%M %p')} EST.")

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")

@client.event
async def on_message(message):
    global last_summary_message

    if message.channel.id != CHANNEL_ID_TRIGGER or message.author == client.user:
        return

    args = message.content.strip().lower().split()
    if len(args) == 2 and args[0] == "!data":
        last_summary_message = await run_trade_summary(mode=args[1], message=message, openai_client=openai_client)
        return

    if args[0] == "!push":
        output_channel = client.get_channel(CHANNEL_ID_SECONDARY_OUTPUT)
        if not output_channel:
            await message.channel.send("❌ Could not find the output channel.")
            return

        # Handle immediate push (no arguments)
        if len(args) == 1:
            if last_summary_message:
                await output_channel.send(last_summary_message)
                await message.channel.send(f"✅ Message has been successfully posted in **{output_channel.name}**.")
            else:
                await message.channel.send("⚠️ No message available to push.")
            return

        # Handle scheduled push
        if len(args) == 2:
            time_arg = args[1]
            
            # Handle "!push open" or "!push close"
            if time_arg == "open":
                target_time = datetime.strptime("09:30", "%H:%M").time()
            elif time_arg == "close":
                target_time = datetime.strptime("16:00", "%H:%M").time()
            else:
                # Validate time format (HH:MM)
                if not re.match(r"^\d{1,2}:\d{2}$", time_arg):
                    await message.channel.send("❌ Invalid time format. Use `!push HH:MM`, `!push open`, or `!push close`.")
                    return
                
                try:
                    target_time = datetime.strptime(time_arg, "%H:%M").time()
                except ValueError:
                    await message.channel.send("❌ Invalid time. Please use a valid 24-hour format (e.g., `16:00`).")
                    return
            
            # Schedule the push
            asyncio.create_task(schedule_push(target_time, message, output_channel))
            return

    if args[0] == "!parse":
        await message.channel.send("🔄 Running parse_signals.py...")
        try:
            await start_parser_bot()
            await message.channel.send("✅ `parse_signals.py` ran successfully.")
        except Exception as e:
            await message.channel.send(f"❌ Exception occurred while running parser: {str(e)}")

# --- Run ---
asyncio.run(client.start(DISCORD_TOKEN))