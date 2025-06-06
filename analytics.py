import re
import json
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio

# Configuration
CONFIG = {
    "output_channel_id": 1379132047783624717,
    "channel_dump_file": "full_channel_dump.txt",
    "channels": {
        "free": "live-signals-free",
        "1": "live-signals-tier-1",
        "2": "live-signals-tier-2",
        "3": "live-signals-tier-3"
    },
    "channel_names": {
        "free": "Free Tier",
        "1": "Tier 1",
        "2": "Tier 2",
        "3": "Tier 3"
    },
    "day_names": {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
}

def get_trading_days(mode, ref_date=None):
    ref_date = ref_date or datetime.today()
    if mode == "today":
        return [ref_date.strftime("%Y-%m-%d")] if ref_date.weekday() < 5 else []
    elif mode == "week":
        weekday = ref_date.weekday()
        start_of_week = ref_date - timedelta(days=weekday)
        days_to_include = 4 if weekday == 3 else (5 if weekday in [5, 6] else weekday + 1)
        if weekday in [5, 6]:
            start_of_week -= timedelta(days=7)
        return [(start_of_week + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(days_to_include)
                if (start_of_week + timedelta(days=i)).weekday() < 5]
    elif mode == "month":
        start_of_month = ref_date.replace(day=1)
        return [(start_of_month + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range((ref_date - start_of_month).days + 1)
                if (start_of_month + timedelta(days=i)).weekday() < 5]
    return []

def build_prompt_for_lines(lines, date_list):
    return f"""
You are a trading assistant. Extract and match real trade signals from chat logs.
Each trade may span multiple days. An entry could happen on one day and the exit on the next.

Return a valid JSON array. Each object must include:
- channel (e.g., "live-signals-tier-3")
- ticker (e.g., "NCIS")
- type (call or put, or null if not specified)
- expiry (or null if not specified)
- entry (e.g., "$1.17" or null if not found)
- exit (e.g., "$2.10" or null if not found)
- status ("open" or "closed")
- summary ("yes" if entry date is in {date_list}, "no" otherwise)
- entry_time (e.g., "2025-06-02 14:38" or null if not found)
- exit_time (e.g., "2025-06-03 11:04" or null if not found)

Rules:
- Match "Exit TICKER @PRICE" or "Exit TICKER" with the most recent unmatched entry of the same ticker in the same channel *only if* the exit time is **after** the entry time.
- If an exit is found without a matching entry in the provided logs, include the trade with status="closed", entry=null, entry_time=null, and summary="no".
- Ignore commentary unless it includes entry/exit details.
- Status is "closed" if an exit is found, "open" if only an entry.
- Use date format YYYY-MM-DD HH:MM for entry_time and exit_time.

Chat Messages:
{''.join(lines)}
"""

def find_entry_in_channel(channel_lines, ticker, exit_time, channel, openai_client):
    try:
        prompt = build_prompt_for_lines(channel_lines, [])
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a trading assistant that processes signals from chat logs."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        cleaned_json = re.sub(r"^```(?:json)?|```$", "", response.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
        trades = json.loads(cleaned_json)

        fmt = "%Y-%m-%d %H:%M"
        dt_exit = datetime.strptime(exit_time, fmt)
        valid_entries = [
            trade for trade in trades
            if trade["ticker"] == ticker
            and trade["channel"] == channel
            and trade["entry_time"]
            and datetime.strptime(trade["entry_time"], fmt) < dt_exit
        ]

        return max(valid_entries, key=lambda x: datetime.strptime(x["entry_time"], fmt)) if valid_entries else None
    except Exception as e:
        print(f"❌ Error searching for entry in channel {channel}: {e}")
        return None

def check_summary_for_inconsistencies(full_message, open_count, trade_details, openai_client):
    prompt = f"""
You are a trading assistant tasked with validating a trade summary message for inconsistencies.
The message contains a summary of trading activity, including total trades, wins, losses, and open positions.
It also lists specific trades grouped by channel or date.

Rules:
- Open positions should **only** be counted in the `open_count` and should **not** appear in the detailed trade list.
- If an open position is incorrectly listed in the trade details, remove it from the trade list and ensure the open_count matches the number of open positions.
- Ensure the total trades count equals the sum of wins, losses, and open positions.
- Preserve the original formatting, including emojis, links, and structure, unless corrections are needed.
- If no inconsistencies are found, return the original message unchanged.
- DO NOT add any note of changes made. Just return the corrected message.

Input:
- Total open positions reported: {open_count}
- Trade details: {json.dumps(trade_details, indent=2)}
- Full message:
{full_message}

Output:
- Return the validated or corrected message as a string.
"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a trading assistant that validates trade summaries for accuracy."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        return re.sub(r"^```(?:json)?|```$", "", response.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
    except Exception as e:
        print(f"❌ Error validating summary: {e}")
        return full_message

def format_trade(trade):
    entry_price = f"${trade['entry']:.2f}" if trade['entry'] is not None else "?"
    trade_str = f"- {trade['ticker']} {trade['type']} @ {entry_price}"
    if trade["status"] == "closed":
        pct_val = trade['percent_change']
        mins = trade['duration'] if trade['duration'] else "unknown"
        emojis = ":fire: :fire: :fire:" if pct_val >= 50 else ":fire: :chart_with_upwards_trend:" if pct_val >= 0 else ""
        pct = f"{pct_val}% gain" if pct_val >= 0 else f"{abs(pct_val)}% loss"
        if trade.get("partial"):
            exits_str = ", ".join(trade["exits"])
            trade_str += f". Sold some at {exits_str} for a {pct} {emojis}"
        else:
            trade_str += f". Sold at {trade['exits'][0]} {mins} later for a {pct} {emojis}"
    return trade_str

async def run_trade_summary(mode, message, openai_client):
    from discord import File
    now = datetime.now()
    date_list = get_trading_days(mode, now)
    if not date_list and mode != "today":
        await message.channel.send(f"❌ Invalid mode. Use `!data today`, `!data week`, or `!data month`.")
        return

    print(f"[Analytics] Starting trade summary for: {mode}")
    await message.channel.send(f":inbox_tray: Collecting messages for `{mode}`...")

    # Read and cache channel lines
    channel_lines = defaultdict(list)
    try:
        with open(CONFIG["channel_dump_file"], "r", encoding="utf-8") as f:
            for line in f:
                for tier, channel in CONFIG["channels"].items():
                    if channel in line:
                        channel_lines[tier].append(line)
                        break
    except FileNotFoundError:
        await message.channel.send("❌ Error: Channel dump file not found.")
        return
    except Exception as e:
        await message.channel.send(f"❌ Error reading channel dump: {str(e)}")
        return

    # Filter lines for the specified date range
    filtered_lines = [line for lines in channel_lines.values() for line in lines if any(f"[{date}" in line for date in date_list)]
    output_filename = now.strftime("%m%d%Y") + f"_{mode}_signals.txt"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.writelines(filtered_lines)

    await message.channel.send("📊 Parsing signals by tier...")
    tiered_lines = {tier: [line for line in lines if any(f"[{date}" in line for date in date_list)] for tier, lines in channel_lines.items()}
    
    all_trades = []
    for i, (tier, lines) in enumerate(tiered_lines.items(), start=1):
        if not lines:
            continue
        print(f"[Step {i}] Prompting Tier {tier}...")
        await message.channel.send(f":robot: Prompting Tier {tier}...")
        prompt = build_prompt_for_lines(lines, date_list)
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a trading assistant that processes signals from chat logs."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0
            )
            cleaned_json = re.sub(r"^```(?:json)?|```$", "", response.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
            trades = json.loads(cleaned_json)
            for trade in trades:
                entry_day = trade["entry_time"].split()[0] if trade["entry_time"] else None
                trade["summary"] = "yes" if entry_day in date_list else "no"
            all_trades.extend(trades)
        except Exception as e:
            print(f"❌ Error parsing tier {tier}: {e}")

    # Process trades with missing entries
    for trade in all_trades:
        if trade["status"] == "closed" and trade["exit_time"] and (not trade["entry_time"] or trade["summary"] == "no"):
            channel = trade["channel"]
            ticker = trade["ticker"]
            exit_time = trade["exit_time"]
            print(f"[Extra Search] Looking for entry for {ticker} in {channel} before {exit_time}")
            await message.channel.send(f":mag_right: Looking for entry for {ticker} in {channel} before {exit_time}")
            tier = next((t for t, c in CONFIG["channels"].items() if c == channel), None)
            if tier:
                entry_trade = find_entry_in_channel(channel_lines[tier], ticker, exit_time, channel, openai_client)
                if entry_trade and entry_trade["entry"] and entry_trade["entry_time"]:
                    trade["entry"] = entry_trade["entry"]
                    trade["entry_time"] = entry_trade["entry_time"]
                    trade["summary"] = "yes"
                    trade["type"] = entry_trade["type"] or trade["type"]
                    trade["expiry"] = entry_trade["expiry"] or trade["expiry"]

    summary_trades = [t for t in all_trades if t.get("summary") == "yes"]
    grouped_trades = defaultdict(list)
    for trade in summary_trades:
        key = (trade["channel"], trade["ticker"], trade["entry_time"])
        grouped_trades[key].append(trade)

    trade_details = []
    win_count = loss_count = open_count = 0
    for (channel, ticker, entry_time), trades in grouped_trades.items():
        try:
            entry = float(trades[0]["entry"].replace("$", "")) if trades[0]["entry"] else None
            if entry is None:
                continue
            exits = []
            for trade in trades:
                if trade.get("status") == "closed" and trade.get("exit"):
                    try:
                        exit_price = float(trade["exit"].replace("$", ""))
                        fmt = "%Y-%m-%d %H:%M"
                        dt_entry = datetime.strptime(trade["entry_time"], fmt)
                        dt_exit = datetime.strptime(trade["exit_time"], fmt)
                        duration = int((dt_exit - dt_entry).total_seconds() / 60)
                        exits.append({
                            "exit": exit_price,
                            "change": ((exit_price - entry) / entry) * 100,
                            "duration": duration
                        })
                    except Exception as e:
                        print(f"⚠️ Error processing exit for {ticker}: {e}")
                        continue
            if exits:
                avg_change = sum(e["change"] for e in exits) / len(exits)
                avg_duration = sum(e["duration"] for e in exits) / len(exits)
                trade_details.append({
                    "channel": channel,
                    "ticker": ticker,
                    "type": trades[0]["type"],
                    "entry": entry,
                    "percent_change": round(avg_change, 2),
                    "duration": f"{int(avg_duration)}m",
                    "status": "closed",
                    "partial": len(exits) > 1,
                    "exits": [f"${e['exit']}" for e in exits],
                    "entry_date": trades[0]["entry_time"].split()[0]
                })
                if avg_change > 0:
                    win_count += 1
                else:
                    loss_count += 1
            else:
                open_count += 1
                trade_details.append({
                    "channel": channel,
                    "ticker": ticker,
                    "type": trades[0]["type"],
                    "entry": entry,
                    "percent_change": 0.0,
                    "duration": "0m",
                    "status": "open",
                    "partial": False,
                    "exits": [],
                    "entry_date": trades[0]["entry_time"].split()[0]
                })
        except Exception as e:
            print(f"⚠️ Skipping grouped trade due to error: {e}")

    closed_trades = [t for t in trade_details if t["status"] == "closed"]
    avg_percent_increase = round(sum(t["percent_change"] for t in closed_trades) / len(closed_trades), 2) if closed_trades else 0.00
    total_profit = sum(sum(float(e.replace("$", "")) for e in t["exits"]) / len(t["exits"]) - t["entry"] for t in closed_trades)
    total_profit = round(total_profit, 2)

    # Format summary
    win_label = "Win" if win_count == 1 else "Wins"
    loss_label = "Loss" if loss_count == 1 else "Losses"
    open_label = "Open Position" if open_count == 1 else "Open Positions"
    
    if mode == "week":
        trades_by_day = defaultdict(list)
        for t in trade_details:
            trades_by_day[t["entry_date"]].append(t)
        summary_title = f"**Weekly Trade Summary for {now.strftime('%m/%d/%Y')} @everyone**"
        full_message = f"{summary_title}\n\n"
        full_message += f"Total Trades: {win_count + loss_count + open_count} ({win_count} {win_label}, {loss_count} {loss_label}, {open_count} {open_label})\n"
        full_message += f"Average Percent Increase: {avg_percent_increase}%\n\n"
        for date in sorted(trades_by_day.keys()):
            day_trades = trades_by_day[date]
            day_closed = [t for t in day_trades if t["status"] == "closed"]
            day_wins = len([t for t in day_closed if t["percent_change"] > 0])
            day_losses = len([t for t in day_closed if t["percent_change"] <= 0])
            day_avg_percent = round(sum(t["percent_change"] for t in day_closed) / len(day_closed), 2) if day_closed else 0.00
            dt = datetime.strptime(date, "%Y-%m-%d")
            formatted_date = f"{CONFIG['day_names'][dt.weekday()]} ({dt.strftime('%m/%d/%Y')}):"
            full_message += f"{formatted_date}\n"
            full_message += f"- Total Trades: {len(day_trades)} ({day_wins} {'Win' if day_wins == 1 else 'Wins'}, {day_losses} {'Loss' if day_losses == 1 else 'Losses'})\n"
            full_message += f"- Average Percent Increase: {day_avg_percent}%\n\n"
        full_message += f"If you bought one contract for each trade this week, you would've made ${total_profit:.2f}\n\n"
    else:
        channel_grouped = defaultdict(list)
        for t in trade_details:
            channel_grouped[t["channel"]].append(t)
        summary_title = f"**{'Daily' if mode == 'today' else 'Monthly'} Trade Summary for {now.strftime('%m/%d/%Y' if mode == 'today' else '%B')} @everyone**"
        full_message = f"{summary_title}\n\n"
        full_message += f"Total Trades: {win_count + loss_count + open_count} ({win_count} {win_label}, {loss_count} {loss_label}, {open_count} {open_label})\n"
        full_message += f"Average Percent Increase: {avg_percent_increase}%\n\n"
        for ch, trades in channel_grouped.items():
            normalized_ch = next((t for t, c in CONFIG["channels"].items() if c == ch), "unknown")
            ch_name = CONFIG["channel_names"].get(normalized_ch, f"Tier {normalized_ch}")
            full_message += f"{ch_name}:\n"
            for t in trades:
                full_message += format_trade(t) + "\n"
            full_message += "\n"

    full_message += "**:closed_lock_with_key: Want to see our open trades?** [Get a premium membership!](https://discord.com/channels/1350549258310385694/1372399067514011749)\n"
    full_message = check_summary_for_inconsistencies(full_message, open_count, trade_details, openai_client)

    if output_channel := message.guild.get_channel(CONFIG["output_channel_id"]):
        await output_channel.send(full_message)
    else:
        await message.channel.send("❌ Error: Output channel not found.")

    print("✅ Trade summary complete.")
    return full_message