"""
main.py — Telegram Scanner Bot: SMA25 + Smart Trail 1H
Deploy on Railway via GitHub.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import ccxt
from dotenv import load_dotenv
from telegram import Bot, BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from scanner import scan_symbols_async
from formatter import (
    format_scan_header,
    format_signal_card,
    format_no_signal_message,
)

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")

# ─────────────────────────────────────────────────────────────────────────────
# Config from environment
# ─────────────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]        # required
CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]          # required
SCAN_MINS   = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
SEND_WEAK   = os.getenv("SEND_WEAK_SIGNALS", "true").lower() == "true"
MARKET_TYPE = os.getenv("MARKET_TYPE", "spot")        # spot | future
BINANCE_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SEC = os.getenv("BINANCE_SECRET", "")

_DEFAULT_COINS = (
    "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,"
    "ADA/USDT,AVAX/USDT,DOT/USDT,MATIC/USDT,LINK/USDT,"
    "UNI/USDT,ATOM/USDT,LTC/USDT,BCH/USDT,FIL/USDT,"
    "APT/USDT,ARB/USDT,OP/USDT,SUI/USDT,INJ/USDT,"
    "NEAR/USDT,FTM/USDT,SAND/USDT,MANA/USDT,AXS/USDT"
)
SYMBOLS: list[str] = [
    s.strip() for s in os.getenv("TOP_COINS", _DEFAULT_COINS).split(",") if s.strip()
]

TZ_VN = timezone(timedelta(hours=7))


# ─────────────────────────────────────────────────────────────────────────────
# Exchange factory
# ─────────────────────────────────────────────────────────────────────────────

def make_exchange() -> ccxt.Exchange:
    opts: dict = {"enableRateLimit": True}
    if MARKET_TYPE == "future":
        opts["options"] = {"defaultType": "future"}
    if BINANCE_KEY:
        opts["apiKey"] = BINANCE_KEY
        opts["secret"] = BINANCE_SEC
    return ccxt.binance(opts)


# ─────────────────────────────────────────────────────────────────────────────
# Core scan job
# ─────────────────────────────────────────────────────────────────────────────

async def run_scan(bot: Bot) -> None:
    logger.info(f"Starting 1H scan — {len(SYMBOLS)} symbols")

    exchange = make_exchange()
    results  = await scan_symbols_async(exchange, SYMBOLS)

    # ── Summary message ───────────────────────────────────────────────────
    summary = format_scan_header(results, len(SYMBOLS), SCAN_MINS)
    await _send(bot, summary)

    if not results:
        return

    # ── Detail cards ──────────────────────────────────────────────────────
    for r in results:
        if "CONFIRMED" in r["signal"]:
            card = format_signal_card(r)
            await _send(bot, card)
            await asyncio.sleep(0.6)
        elif SEND_WEAK and "WEAK" in r["signal"]:
            card = format_signal_card(r)
            await _send(bot, card)
            await asyncio.sleep(0.6)

    logger.info(f"Scan complete — {len(results)} signals found")


async def _send(bot: Bot, text: str) -> None:
    """Send message, split if >4096 chars."""
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            logger.error(f"Telegram send error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Telegram command handlers (simple polling-free inline handling)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_updates(bot: Bot, offset: int) -> int:
    """Minimal update loop for /scan command support."""
    updates = await bot.get_updates(offset=offset, timeout=10, allowed_updates=["message"])
    for upd in updates:
        offset = upd.update_id + 1
        msg = upd.message
        if not msg or not msg.text:
            continue
        text = msg.text.strip().lower()
        chat = str(msg.chat_id)

        if chat != str(CHAT_ID):
            continue  # only respond to configured chat

        if text in ("/scan", "/quét", "/quet"):
            await bot.send_message(chat_id=CHAT_ID, text="🔄 Đang quét 1H… vui lòng chờ")
            await run_scan(bot)

        elif text in ("/status", "/ping"):
            tz_vn = timezone(timedelta(hours=7))
            now   = datetime.now(tz_vn).strftime("%H:%M:%S %d/%m/%Y")
            await bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"✅ *Bot đang chạy*\n"
                    f"📡 Quét mỗi `{SCAN_MINS}` phút\n"
                    f"📊 `{len(SYMBOLS)}` symbols\n"
                    f"🕐 {now} (GMT+7)"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif text in ("/list", "/coins"):
            coins_str = "\n".join(f"  • `{s}`" for s in SYMBOLS)
            await bot.send_message(
                chat_id=CHAT_ID,
                text=f"📋 *Danh sách coins đang quét:*\n{coins_str}",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif text in ("/help", "/start"):
            await bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    "🤖 *SMA25 + Smart Trail Bot v4*\n\n"
                    "Lệnh hỗ trợ:\n"
                    "/scan — Quét ngay lập tức\n"
                    "/status — Kiểm tra trạng thái bot\n"
                    "/list — Xem danh sách coins\n"
                    "/help — Hướng dẫn sử dụng\n\n"
                    "Bot tự động quét mỗi giờ trên khung 1H.\n"
                    "Tín hiệu dựa trên: SMA25 + ATR Trail + SQZ Momentum"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
    return offset


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("Initialising bot…")
    bot = Bot(token=BOT_TOKEN)

    # Set bot commands
    try:
        await bot.set_my_commands([
            BotCommand("scan",   "Quét tín hiệu ngay lập tức"),
            BotCommand("status", "Kiểm tra trạng thái bot"),
            BotCommand("list",   "Danh sách coins đang quét"),
            BotCommand("help",   "Hướng dẫn sử dụng"),
        ])
    except TelegramError:
        pass

    # Startup message
    await _send(
        bot,
        (
            f"🤖 *SMA25 + Smart Trail Bot v4 — Khởi động*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Số coins:   `{len(SYMBOLS)}`\n"
            f"⏱ Interval:   `{SCAN_MINS}` phút\n"
            f"📈 Market:     `{MARKET_TYPE.upper()}`\n"
            f"📡 Timeframe:  `1H`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Bot sẵn sàng!  Gõ /help để xem lệnh"
        ),
    )

    # Scheduler: run every SCAN_MINS minutes
    scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")
    scheduler.add_job(
        run_scan,
        trigger="interval",
        minutes=SCAN_MINS,
        args=[bot],
        next_run_time=datetime.now(),   # run immediately on startup
        id="scan_job",
    )
    scheduler.start()
    logger.info(f"Scheduler started — every {SCAN_MINS} min")

    # Update polling loop (for /scan command support)
    offset = 0
    while True:
        try:
            offset = await handle_updates(bot, offset)
        except TelegramError as e:
            logger.warning(f"Update polling error: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error in update loop: {e}")
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
