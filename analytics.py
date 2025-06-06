import re
import json
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio
from parse_signals import start_parser_bot  # ← Added import for parser

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
        if weekday in [5, 6]:
            start_of_week -= timedelta(days=7)
        return [
            (start_of_week + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(5)
            if (start_of_week + timedelta(days=i)).weekday() < 5
        ]
    elif mode == "month":
        start_of_month = ref_date.replace(day=1)
        days = (ref_date - start_of_month).days + 1
        return [
            (start_of_month + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(days)
            if (start_of_month + timedelta(days=i)).weekday() < 5
        ]
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
- summary ("yes" if the trade’s relevant date—exit for closed, entry for open—is in {date_list}, otherwise "no". to clarify, "summary" should be set to yes if an open trade with the entry date is found or if a closed trade with the exit date is found)
- entry_time (e.g., "2025-06-02 14:38" or null if not found)
- exit_time (e.g., "2025-06-03 11:04" or null if not found)

Rules:
- Only interpret lines that explicitly say “Entry TICKER @PRICE” or “Exit TICKER @PRICE” (or very close variants) as actual signals.
- Ignore messages that look like “guidance” or “reminders,” for example anything starting with phrases like:
    • “Whoever didn’t exit”
    • “Just in case you didn’t exit”
    • “If you haven’t exited”
  These are not new Exit signals—they’re just commentary referencing a previous exit.
- Match "Exit TICKER @PRICE" or "Exit TICKER" with the most recent unmatched entry of the same ticker in the same channel only if the exit time is after the entry time.
- If an exit is found without a matching entry in the provided logs, include the trade with status="closed", entry=null, entry_time=null, and summary="no".
- Ignore any other commentary that does not include explicit entry/exit details.
- Status is "closed" if an exit is found; "open" if only an entry is present.
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

        # Normalize ticker comparison to be case-insensitive
        ticker_upper = ticker.upper()
        valid_entries = [
            trade for trade in trades
            if trade["ticker"].upper() == ticker_upper
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
    # ─────────────────────────────────────────────────────────────────────────────
    # FIRST THING: Run parse_signals.py when !data is invoked
    #await message.channel.send("🔄 Running parse_signals.py...")
    #try:
    #    await start_parser_bot()
    #    await message.channel.send("✅ `parse_signals.py` ran successfully.")
    #except Exception as e:
    #    await message.channel.send(f"❌ Exception occurred while running parser: {str(e)}")
    #    return
    # ─────────────────────────────────────────────────────────────────────────────

    from discord import File
    now = datetime.now()
    date_list = get_trading_days(mode, now)
    if not date_list and mode != "today":
        await message.channel.send(f"❌ Invalid mode. Use `!data today`, `!data week`, or `!data month`.")
        return

    print(f"[Analytics] Starting trade summary for: {mode}")
    await message.channel.send(f":inbox_tray: Collecting messages for `{mode}`...")

    # 1) Read full channel dump (all timestamps), store by tier.
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

    # 2) Filter just the lines that mention any date in our week/month,
    #    so we can send those to the LLM to extract trades.
    filtered_lines = [
        line
        for lines in channel_lines.values()
        for line in lines
        if any(f"[{date}" in line for date in date_list)
    ]
    output_filename = now.strftime("%m%d%Y") + f"_{mode}_signals.txt"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.writelines(filtered_lines)

    await message.channel.send("📊 Parsing signals by tier...")

    # 3) For each tier, keep only those lines in date_list (this is what we feed to the LLM)
    tiered_lines = {
        tier: [
            line for line in lines
            if any(f"[{date}" in line for date in date_list)
        ]
        for tier, lines in channel_lines.items()
    }

    # ─── NEW FIX ─────────────────────────────────────────────────────────────────
    # Normalize any “TICKER … EOD @PRICE” lines into “Entry TICKER @PRICE”
    for tier, lines in tiered_lines.items():
        normalized = []
        for line in lines:
            # If a line contains “EOD @” but does not already include “Entry”, convert it
            if " EOD @" in line and "entry" not in line.lower():
                # Example: “[2025-06-06 12:50] ...: TEM 63C EOD @0.45$ ...”
                # → “[2025-06-06 12:50] ...: Entry TEM 63C @0.45$ ...”
                normalized_line = re.sub(
                    r"(\b[A-Z0-9]{1,5}\s*\d+[CP]\b)\s+EOD\s+@",
                    r"Entry \1 @",
                    line,
                    flags=re.IGNORECASE
                )
                normalized.append(normalized_line)
            else:
                normalized.append(line)
        tiered_lines[tier] = normalized
    # ─────────────────────────────────────────────────────────────────────────────

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
            print(trades)

            # *** FIX #1: Summary‐flagging now uses exit_date for closed trades ***
            for trade in trades:
                entry_day = trade["entry_time"].split()[0] if trade["entry_time"] else None
                exit_day  = trade["exit_time"].split()[0] if trade["exit_time"] else None

                if trade["status"] == "closed":
                    trade["summary"] = "yes" if exit_day in date_list else "no"
                elif trade["status"] == "open":
                    trade["summary"] = "yes"
                else:
                    trade["summary"] = "yes" if entry_day in date_list else "no"

            all_trades.extend(trades)
        except Exception as e:
            print(f"❌ Error parsing tier {tier}: {e}")

    # 4) For each closed trade with no entry (or summary=="no" but exit_in_week),
    #    do an “extra search” over the FULL channel dump (all dates).
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
                    # *** FIX #2: No longer require entry_day ∈ date_list ***
                    trade["entry"] = entry_trade["entry"]
                    trade["entry_time"] = entry_trade["entry_time"]
                    # We already know exit_day is in date_list (otherwise summary would be "no" earlier)
                    trade["summary"] = "yes"
                    trade["type"]   = entry_trade["type"]   or trade["type"]
                    trade["expiry"] = entry_trade["expiry"] or trade["expiry"]
                else:
                    print(f"⚠️ Warning: entry missing for {ticker} closed at {exit_time}. Skipping.")

    # 5) Keep exactly those trades whose “relevant date” sits in date_list
    summary_trades = [t for t in all_trades if t.get("summary") == "yes"]

    # 6) Group by (channel, ticker, entry_time) so that partial exits merge into one trade
    grouped_trades = defaultdict(list)
    for trade in summary_trades:
        key = (trade["channel"], trade["ticker"], trade["entry_time"])
        grouped_trades[key].append(trade)

    trade_details = []
    win_count = loss_count = open_count = 0

    for (channel, ticker, entry_time), trades in grouped_trades.items():
        try:
            # Try to parse entry_price; if None, that means we never found an entry.
            entry_price = float(trades[0]["entry"].replace("$", "")) if trades[0]["entry"] else None
            if entry_price is None:
                print(f"⚠️ Warning: entry missing for {ticker} closed at {trades[0]['exit_time']}. Skipping.")
                continue

            exits = []
            for tr in trades:
                if tr.get("status") == "closed" and tr.get("exit"):
                    try:
                        exit_price = float(tr["exit"].replace("$", ""))
                        fmt = "%Y-%m-%d %H:%M"
                        dt_entry = datetime.strptime(tr["entry_time"], fmt)
                        dt_exit  = datetime.strptime(tr["exit_time"], fmt)
                        duration = int((dt_exit - dt_entry).total_seconds() / 60)
                        exits.append({
                            "exit": exit_price,
                            "change": ((exit_price - entry_price) / entry_price) * 100,
                            "duration": duration,
                            "exit_date": tr["exit_time"].split()[0]
                        })
                    except Exception as e:
                        print(f"⚠️ Error processing exit for {ticker}: {e}")
                        continue

            if exits:
                # Closed trade: use the last exit_date as our “trade_date”
                last_exit = exits[-1]
                trade_date = last_exit["exit_date"]
                avg_change   = sum(e["change"] for e in exits) / len(exits)
                avg_duration = sum(e["duration"] for e in exits) / len(exits)
                trade_details.append({
                    "channel": channel,
                    "ticker": ticker,
                    "type": trades[0]["type"],
                    "entry": entry_price,
                    "percent_change": round(avg_change, 2),
                    "duration": f"{int(avg_duration)}m",
                    "status": "closed",
                    "partial": len(exits) > 1,
                    "exits": [f"${e['exit']}" for e in exits],
                    "trade_date": trade_date
                })
                if avg_change > 0:
                    win_count += 1
                else:
                    loss_count += 1
            else:
                # Open trade
                entry_date = trades[0]["entry_time"].split()[0]
                trade_details.append({
                    "channel": channel,
                    "ticker": ticker,
                    "type": trades[0]["type"],
                    "entry": entry_price,
                    "percent_change": 0.0,
                    "duration": "0m",
                    "status": "open",
                    "partial": False,
                    "exits": [],
                    "trade_date": entry_date
                })
                open_count += 1

        except Exception as e:
            print(f"⚠️ Skipping grouped trade due to error: {e}")

    # 7) Compute aggregates
    closed_trades = [t for t in trade_details if t["status"] == "closed"]

    # Weighted average percent increase
    if closed_trades:
        total_weight = sum(t["entry"] for t in closed_trades)
        if total_weight > 0:
            weighted_sum = sum(t["percent_change"] * t["entry"] for t in closed_trades)
            avg_percent_increase = round(weighted_sum / total_weight, 2)
        else:
            avg_percent_increase = 0.00
    else:
        avg_percent_increase = 0.00

    total_profit = sum(
        sum(float(e.replace("$", "")) for e in t["exits"]) / len(t["exits"]) - t["entry"]
        for t in closed_trades
    )
    total_profit = round(total_profit, 2)

    win_label  = "Win" if win_count == 1 else "Wins"
    loss_label = "Loss" if loss_count == 1 else "Losses"

    # 8) Build final summary text
    if mode == "week":
        trades_by_day = defaultdict(list)
        for t in trade_details:
            trades_by_day[t["trade_date"]].append(t)

        summary_title = f"**Weekly Trade Summary for {now.strftime('%m/%d/%Y')} @everyone**"
        full_message = f"{summary_title}\n\n"
        total_trades = win_count + loss_count
        full_message += (
            f"Total Trades: {total_trades} "
            f"({win_count} {win_label}, {loss_count} {loss_label})\n"
        )
        full_message += f"Average Percent Increase: {avg_percent_increase}%\n\n"

        for date in sorted(trades_by_day.keys()):
            if date not in date_list:
                continue

            day_trades  = trades_by_day[date]
            day_closed  = [t for t in day_trades if t["status"] == "closed"]
            day_wins    = len([t for t in day_closed if t["percent_change"] > 0])
            day_losses  = len([t for t in day_closed if t["percent_change"] <= 0])

            # Weighted average per day
            if day_closed:
                day_weight = sum(t["entry"] for t in day_closed)
                if day_weight > 0:
                    day_weighted_sum = sum(t["percent_change"] * t["entry"] for t in day_closed)
                    day_avg_pct = round(day_weighted_sum / day_weight, 2)
                else:
                    day_avg_pct = 0.00
            else:
                day_avg_pct = 0.00

            dt = datetime.strptime(date, "%Y-%m-%d")
            formatted_date = f"{CONFIG['day_names'][dt.weekday()]} ({dt.strftime('%m/%d/%Y')}):"
            full_message += f"{formatted_date}\n"
            full_message += (
                f"- Total Trades: {len(day_trades)} "
                f"({day_wins} {'Win' if day_wins == 1 else 'Wins'}, {day_losses} "
                f"{'Loss' if day_losses == 1 else 'Losses'})\n"
            )
            full_message += f"- Average Percent Increase: {day_avg_pct}%\n\n"

        profit_cents = int(total_profit * 100)
        full_message += f"If you bought one contract for each trade this week, you would've made ${profit_cents}\n\n"

    else:
        # Daily or Monthly
        channel_grouped = defaultdict(list)
        for t in trade_details:
            channel_grouped[t["channel"]].append(t)

        is_monthly = (mode == "month")
        summary_title = (
            f"**{'Daily' if mode == 'today' else 'Monthly'} Trade Summary for "
            f"{now.strftime('%m/%d/%Y' if mode == 'today' else '%B')} @everyone**"
        )
        full_message = f"{summary_title}\n\n"

        if is_monthly:
            total_trades = win_count + loss_count
            full_message += (
                f"Total Trades: {total_trades} "
                f"({win_count} {win_label}, {loss_count} {loss_label})\n"
            )
        else:
            # For "today", include open positions so total = wins + losses + open_count
            total_trades = win_count + loss_count + open_count
            full_message += (
                f"Total Trades: {total_trades} "
                f"({win_count} {win_label}, {loss_count} {loss_label}, {open_count} Open {'Position' if open_count==1 else 'Positions'})\n"
            )

        full_message += f"Average Percent Increase: {avg_percent_increase}%\n\n"

        for ch, trades in channel_grouped.items():
            normalized_ch = next((t for t, c in CONFIG["channels"].items() if c == ch), "unknown")
            ch_name = CONFIG["channel_names"].get(normalized_ch, f"Tier {normalized_ch}")
            full_message += f"{ch_name}:\n"
            for t in trades:
                full_message += format_trade(t) + "\n"
            full_message += "\n"

        if is_monthly:
            profit_cents = int(total_profit * 100)
            full_message += f"If you bought one contract for each trade this month, you would've made ${profit_cents}\n\n"

    full_message += (
        ":closed_lock_with_key: Want to see our open trades? "
        "[Get a premium membership!](https://discord.com/channels/1350549258310385694/1372399067514011749)\n"
    )
    full_message = check_summary_for_inconsistencies(full_message, open_count, trade_details, openai_client)

    if output_channel := message.guild.get_channel(CONFIG["output_channel_id"]):
        await output_channel.send(full_message)
    else:
        await message.channel.send("❌ Error: Output channel not found.")

    print("✅ Trade summary complete.")
    return full_message
