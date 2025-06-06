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
CHANNEL_ID_TERTIARY_OUTPUT = 123456789012345678  # ← replace with your live output channel ID

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
    # Print to console
    print(f"✅ Logged in as {client.user}")
    
    # Also send a “bot is online” message into the trigger channel
    trigger_channel = client.get_channel(CHANNEL_ID_TRIGGER)
    if trigger_channel:
        await trigger_channel.send(f"✅ Logged in as {client.user}")
    else:
        print("⚠️ Could not find the trigger channel to send login confirmation.")

@client.event
async def on_message(message):
    global last_summary_message

    # Only respond in the trigger channel and ignore self-messages
    if message.channel.id != CHANNEL_ID_TRIGGER or message.author == client.user:
        return

    args = message.content.strip().lower().split()

    # === DATA command: store last_summary_message ===
    if len(args) == 2 and args[0] == "!data":
        last_summary_message = await run_trade_summary(
            mode=args[1],
            message=message,
            openai_client=openai_client
        )
        return

    # === PUSH command: immediate or scheduled to test/live channels ===
    if args[0] == "!push":
        # Decide which output channel: "live" → tertiary, otherwise → secondary
        if len(args) >= 2 and args[1] == "live":
            output_channel = client.get_channel(CHANNEL_ID_TERTIARY_OUTPUT)
        else:
            # Defaults to secondary for "test" or no second argument
            output_channel = client.get_channel(CHANNEL_ID_SECONDARY_OUTPUT)
        
        if not output_channel:
            await message.channel.send("❌ Could not find the output channel.")
            return

        # If only "!push" or "!push test"/"!push live" without time
        if len(args) == 1 or (len(args) == 2 and args[1] in ["test", "live"]):
            if last_summary_message:
                await output_channel.send(last_summary_message)
                await message.channel.send(f"✅ Message posted in **{output_channel.name}**.")
            else:
                await message.channel.send("⚠️ No message available to push.")
            return

        # Scheduled push: "!push test close", "!push live 16:00", etc.
        if len(args) == 3 and args[1] in ["test", "live"]:
            time_arg = args[2]
            
            if time_arg == "open":
                target_time = datetime.strptime("09:30", "%H:%M").time()
            elif time_arg == "close":
                target_time = datetime.strptime("16:00", "%H:%M").time()
            else:
                if not re.match(r"^\d{1,2}:\d{2}$", time_arg):
                    await message.channel.send(
                        "❌ Invalid time format. Use `!push <test|live> HH:MM`, "
                        "`!push <test|live> open`, or `!push <test|live> close`."
                    )
                    return
                try:
                    target_time = datetime.strptime(time_arg, "%H:%M").time()
                except ValueError:
                    await message.channel.send(
                        "❌ Invalid time. Please use a valid 24-hour format (e.g., `16:00`)."
                    )
                    return
            
            asyncio.create_task(schedule_push(target_time, message, output_channel))
            return

        await message.channel.send(
            "❌ Invalid usage. Examples:\n"
            "`!push test`\n"
            "`!push live close`\n"
            "`!push test 14:30`"
        )
        return

    # === KILL command: shut down the bot ===
    if args[0] == "!kill":
        await message.channel.send("🔌 Shutting down...")
        await client.close()
        return

    # === PARSE command: run parser bot ===
    if args[0] == "!parse":
        await message.channel.send("🔄 Running parse_signals.py...")
        try:
            await start_parser_bot()
            await message.channel.send("✅ `parse_signals.py` ran successfully.")
        except Exception as e:
            await message.channel.send(f"❌ Exception occurred while running parser: {str(e)}")
        return

# --- Run the client ---
asyncio.run(client.start(DISCORD_TOKEN))
