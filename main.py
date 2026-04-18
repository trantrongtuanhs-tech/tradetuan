"""
main.py — Telegram Scanner Bot: SMA25 + Smart Trail 1H
Scans top-500 tokens by 24h USDT volume from Binance.
Deploy on Railway via GitHub.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import ccxt
from dotenv import load_dotenv
from telegram import Bot, BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from scanner import get_top_symbols, scan_symbols_async
from formatter import (
    format_scan_header,
    format_signal_card,
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
# Config
# ─────────────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID       = os.environ["TELEGRAM_CHAT_ID"]
SCAN_MINS     = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
TOP_N         = int(os.getenv("TOP_N_COINS", "500"))
MIN_VOL_M     = float(os.getenv("MIN_VOLUME_USDT_M", "0.5"))   # $0.5M = 500k
CONCURRENCY   = int(os.getenv("SCAN_CONCURRENCY", "8"))
SEND_WEAK     = os.getenv("SEND_WEAK_SIGNALS", "true").lower() == "true"
MARKET_TYPE   = os.getenv("MARKET_TYPE", "spot")
BINANCE_KEY   = os.getenv("BINANCE_API_KEY", "")
BINANCE_SEC   = os.getenv("BINANCE_SECRET", "")
# Refresh symbol list every N scans (0 = every scan)
SYMBOL_REFRESH= int(os.getenv("SYMBOL_REFRESH_EVERY", "6"))

TZ_VN = timezone(timedelta(hours=7))

# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────
_symbols:      list = []
_scan_count:   int  = 0
_last_sym_refresh: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Exchange factory
# ─────────────────────────────────────────────────────────────────────────────

def make_exchange() -> ccxt.Exchange:
    opts = {"enableRateLimit": True}
    if MARKET_TYPE == "future":
        opts["options"] = {"defaultType": "future"}
    if BINANCE_KEY:
        opts["apiKey"] = BINANCE_KEY
        opts["secret"] = BINANCE_SEC
    return ccxt.binance(opts)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _send(bot: Bot, text: str) -> None:
    """Send message, split at 4096 chars if needed."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        try:
            await bot.send_message(
                chat_id=CHAT_ID, text=chunk,
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            logger.error(f"Telegram send error: {e}")


def _now_vn() -> str:
    return datetime.now(TZ_VN).strftime("%H:%M:%S  %d/%m/%Y")


async def _refresh_symbols(exchange: ccxt.Exchange) -> None:
    global _symbols, _last_sym_refresh
    _symbols = await asyncio.to_thread(
        get_top_symbols, exchange, "USDT", TOP_N, MIN_VOL_M * 1_000_000
    )
    _last_sym_refresh = time.time()
    logger.info(f"Symbol list refreshed: {len(_symbols)} tokens")


# ─────────────────────────────────────────────────────────────────────────────
# Core scan job
# ─────────────────────────────────────────────────────────────────────────────

async def run_scan(bot: Bot) -> None:
    global _scan_count
    _scan_count += 1

    exchange = make_exchange()

    # Refresh symbol list on first run or every SYMBOL_REFRESH scans
    if not _symbols or (SYMBOL_REFRESH > 0 and _scan_count % SYMBOL_REFRESH == 1):
        await _send(bot, f"🔄 Cập nhật danh sách top-{TOP_N} tokens theo volume 24h…")
        await _refresh_symbols(exchange)

    total = len(_symbols)
    logger.info(f"Scan #{_scan_count} — {total} symbols  (concurrency={CONCURRENCY})")

    # Progress tracker: send update every 100 symbols
    progress_msg_id: dict = {"last": 0}

    async def _progress(done: int, total: int):
        if done % 100 == 0 or done == total:
            pct = done / total * 100
            bar = "█" * (done // 50) + "░" * ((total - done) // 50)
            logger.info(f"  Progress: {done}/{total} ({pct:.0f}%)")
            # Only log, don't spam Telegram for every 100

    start_t = time.time()

    # Notify scan start
    await _send(
        bot,
        f"🔍 *Bắt đầu quét #{_scan_count}*\n"
        f"📊 `{total}` tokens  |  ⚡ {CONCURRENCY} luồng song song\n"
        f"🕐 {_now_vn()}",
    )

    results = await scan_symbols_async(exchange, _symbols, CONCURRENCY, _progress)

    elapsed = time.time() - start_t
    logger.info(f"Scan #{_scan_count} done in {elapsed:.1f}s — {len(results)} signals")

    # ── Summary ───────────────────────────────────────────────────────────
    summary = format_scan_header(results, total, SCAN_MINS)
    summary += f"\n⏱ Thời gian quét: `{elapsed:.0f}s`"
    await _send(bot, summary)

    # ── Detail cards for CONFIRMED signals ───────────────────────────────
    confirmed = [r for r in results if "CONFIRMED" in r["signal"]]
    weak      = [r for r in results if "WEAK" in r["signal"]]

    for r in confirmed:
        await _send(bot, format_signal_card(r))
        await asyncio.sleep(0.5)

    if SEND_WEAK and weak:
        # Batch weak signals into one message instead of flooding
        lines = ["⚠️ *Tín hiệu WEAK (cần xác nhận thêm):*"]
        for r in weak[:20]:   # cap at 20 weak signals
            em = "📈" if "BUY" in r["signal"] else "📉"
            lines.append(
                f"{em} `{r['symbol']}` — RSI {r['rsi']} | ADX {r['adx']}"
                f"{'⚡' if r['adx_strong'] else ''} | SQZ {r['sqz_verdict']}"
            )
        if len(weak) > 20:
            lines.append(f"_...và {len(weak)-20} tín hiệu weak khác_")
        await _send(bot, "\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Command handler (polling)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_updates(bot: Bot, offset: int) -> int:
    updates = await bot.get_updates(offset=offset, timeout=10, allowed_updates=["message"])
    for upd in updates:
        offset = upd.update_id + 1
        msg = upd.message
        if not msg or not msg.text:
            continue
        if str(msg.chat_id) != str(CHAT_ID):
            continue

        cmd = msg.text.strip().lower().split()[0]

        if cmd in ("/scan", "/quet", "/quét"):
            await _send(bot, f"🔄 Đang quét `{len(_symbols) or TOP_N}` tokens…")
            await run_scan(bot)

        elif cmd in ("/status", "/ping"):
            await _send(
                bot,
                f"✅ *Bot đang chạy*\n"
                f"📡 Interval: `{SCAN_MINS}` phút\n"
                f"📊 Tokens: `{len(_symbols)}`\n"
                f"⚡ Concurrency: `{CONCURRENCY}` luồng\n"
                f"🔄 Scan count: `{_scan_count}`\n"
                f"🕐 {_now_vn()} (GMT+7)",
            )

        elif cmd in ("/top", "/coins", "/list"):
            arg = msg.text.strip().split()
            n   = int(arg[1]) if len(arg) > 1 and arg[1].isdigit() else 30
            n   = min(n, 100)
            if _symbols:
                lines = [f"📋 *Top {n} / {len(_symbols)} tokens đang quét:*"]
                for i, s in enumerate(_symbols[:n], 1):
                    lines.append(f"  {i:>3}. `{s}`")
                await _send(bot, "\n".join(lines))
            else:
                await _send(bot, "⏳ Chưa có danh sách — đang chờ lần quét đầu")

        elif cmd in ("/refresh", "/update"):
            exchange = make_exchange()
            await _send(bot, f"🔄 Đang cập nhật top-{TOP_N} tokens…")
            await _refresh_symbols(exchange)
            await _send(bot, f"✅ Đã cập nhật: `{len(_symbols)}` tokens")

        elif cmd in ("/help", "/start"):
            await _send(
                bot,
                "🤖 *SMA25 + Smart Trail Bot v4 — 500 Tokens*\n\n"
                "Lệnh hỗ trợ:\n"
                "`/scan`     — Quét ngay lập tức\n"
                "`/status`   — Trạng thái bot\n"
                "`/top [N]`  — Xem top N coins đang quét\n"
                "`/refresh`  — Cập nhật lại danh sách tokens\n"
                "`/help`     — Hướng dẫn\n\n"
                f"⏱ Tự động quét mỗi `{SCAN_MINS}` phút\n"
                f"📊 Quét `{TOP_N}` tokens top volume 24h\n"
                "📡 Tín hiệu: SMA25 + ATR Trail + SQZ 1H",
            )
    return offset


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("Initialising bot…")
    bot = Bot(token=BOT_TOKEN)

    try:
        await bot.set_my_commands([
            BotCommand("scan",    "Quét tín hiệu ngay lập tức"),
            BotCommand("status",  "Trạng thái bot"),
            BotCommand("top",     "Top N coins đang quét"),
            BotCommand("refresh", "Cập nhật danh sách tokens"),
            BotCommand("help",    "Hướng dẫn"),
        ])
    except TelegramError:
        pass

    await _send(
        bot,
        f"🤖 *SMA25 + Smart Trail Bot v4 — Khởi động*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Top tokens:   `{TOP_N}` (by 24h volume)\n"
        f"⏱ Interval:     `{SCAN_MINS}` phút\n"
        f"⚡ Concurrency:  `{CONCURRENCY}` luồng\n"
        f"💰 Min volume:   `${MIN_VOL_M:.1f}M` / ngày\n"
        f"📈 Market:       `{MARKET_TYPE.upper()}`\n"
        f"📡 Timeframe:    `1H`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Bot sẵn sàng!  Gõ /help để xem lệnh",
    )

    scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")
    scheduler.add_job(
        run_scan, "interval",
        minutes=SCAN_MINS,
        args=[bot],
        next_run_time=datetime.now(),   # run immediately
        id="scan_job",
    )
    scheduler.start()
    logger.info(f"Scheduler started — every {SCAN_MINS} min")

    offset = 0
    while True:
        try:
            offset = await handle_updates(bot, offset)
        except TelegramError as e:
            logger.warning(f"Update polling: {e}")
        except Exception as e:
            logger.exception(f"Update loop error: {e}")
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
"""
main.py — Telegram Scanner Bot: SMA25 + Smart Trail 1H
Scans top-500 tokens by 24h USDT volume from Binance.
Deploy on Railway via GitHub.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import ccxt
from dotenv import load_dotenv
from telegram import Bot, BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from scanner import get_top_symbols, scan_symbols_async
from formatter import (
    format_scan_header,
    format_signal_card,
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
# Config
# ─────────────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID       = os.environ["TELEGRAM_CHAT_ID"]
SCAN_MINS     = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
TOP_N         = int(os.getenv("TOP_N_COINS", "500"))
MIN_VOL_M     = float(os.getenv("MIN_VOLUME_USDT_M", "0.5"))   # $0.5M = 500k
CONCURRENCY   = int(os.getenv("SCAN_CONCURRENCY", "8"))
SEND_WEAK     = os.getenv("SEND_WEAK_SIGNALS", "true").lower() == "true"
MARKET_TYPE   = os.getenv("MARKET_TYPE", "spot")
BINANCE_KEY   = os.getenv("BINANCE_API_KEY", "")
BINANCE_SEC   = os.getenv("BINANCE_SECRET", "")
# Refresh symbol list every N scans (0 = every scan)
SYMBOL_REFRESH= int(os.getenv("SYMBOL_REFRESH_EVERY", "6"))

TZ_VN = timezone(timedelta(hours=7))

# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────
_symbols:      list = []
_scan_count:   int  = 0
_last_sym_refresh: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Exchange factory
# ─────────────────────────────────────────────────────────────────────────────

def make_exchange() -> ccxt.Exchange:
    opts = {"enableRateLimit": True}
    if MARKET_TYPE == "future":
        opts["options"] = {"defaultType": "future"}
    if BINANCE_KEY:
        opts["apiKey"] = BINANCE_KEY
        opts["secret"] = BINANCE_SEC
    return ccxt.binance(opts)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _send(bot: Bot, text: str) -> None:
    """Send message, split at 4096 chars if needed."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        try:
            await bot.send_message(
                chat_id=CHAT_ID, text=chunk,
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            logger.error(f"Telegram send error: {e}")


def _now_vn() -> str:
    return datetime.now(TZ_VN).strftime("%H:%M:%S  %d/%m/%Y")


async def _refresh_symbols(exchange: ccxt.Exchange) -> None:
    global _symbols, _last_sym_refresh
    _symbols = await asyncio.to_thread(
        get_top_symbols, exchange, "USDT", TOP_N, MIN_VOL_M * 1_000_000
    )
    _last_sym_refresh = time.time()
    logger.info(f"Symbol list refreshed: {len(_symbols)} tokens")


# ─────────────────────────────────────────────────────────────────────────────
# Core scan job
# ─────────────────────────────────────────────────────────────────────────────

async def run_scan(bot: Bot) -> None:
    global _scan_count
    _scan_count += 1

    exchange = make_exchange()

    # Refresh symbol list on first run or every SYMBOL_REFRESH scans
    if not _symbols or (SYMBOL_REFRESH > 0 and _scan_count % SYMBOL_REFRESH == 1):
        await _send(bot, f"🔄 Cập nhật danh sách top-{TOP_N} tokens theo volume 24h…")
        await _refresh_symbols(exchange)

    total = len(_symbols)
    logger.info(f"Scan #{_scan_count} — {total} symbols  (concurrency={CONCURRENCY})")

    # Progress tracker: send update every 100 symbols
    progress_msg_id: dict = {"last": 0}

    async def _progress(done: int, total: int):
        if done % 100 == 0 or done == total:
            pct = done / total * 100
            bar = "█" * (done // 50) + "░" * ((total - done) // 50)
            logger.info(f"  Progress: {done}/{total} ({pct:.0f}%)")
            # Only log, don't spam Telegram for every 100

    start_t = time.time()

    # Notify scan start
    await _send(
        bot,
        f"🔍 *Bắt đầu quét #{_scan_count}*\n"
        f"📊 `{total}` tokens  |  ⚡ {CONCURRENCY} luồng song song\n"
        f"🕐 {_now_vn()}",
    )

    results = await scan_symbols_async(exchange, _symbols, CONCURRENCY, _progress)

    elapsed = time.time() - start_t
    logger.info(f"Scan #{_scan_count} done in {elapsed:.1f}s — {len(results)} signals")

    # ── Summary ───────────────────────────────────────────────────────────
    summary = format_scan_header(results, total, SCAN_MINS)
    summary += f"\n⏱ Thời gian quét: `{elapsed:.0f}s`"
    await _send(bot, summary)

    # ── Detail cards for CONFIRMED signals ───────────────────────────────
    confirmed = [r for r in results if "CONFIRMED" in r["signal"]]
    weak      = [r for r in results if "WEAK" in r["signal"]]

    for r in confirmed:
        await _send(bot, format_signal_card(r))
        await asyncio.sleep(0.5)

    if SEND_WEAK and weak:
        # Batch weak signals into one message instead of flooding
        lines = ["⚠️ *Tín hiệu WEAK (cần xác nhận thêm):*"]
        for r in weak[:20]:   # cap at 20 weak signals
            em = "📈" if "BUY" in r["signal"] else "📉"
            lines.append(
                f"{em} `{r['symbol']}` — RSI {r['rsi']} | ADX {r['adx']}"
                f"{'⚡' if r['adx_strong'] else ''} | SQZ {r['sqz_verdict']}"
            )
        if len(weak) > 20:
            lines.append(f"_...và {len(weak)-20} tín hiệu weak khác_")
        await _send(bot, "\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Command handler (polling)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_updates(bot: Bot, offset: int) -> int:
    updates = await bot.get_updates(offset=offset, timeout=10, allowed_updates=["message"])
    for upd in updates:
        offset = upd.update_id + 1
        msg = upd.message
        if not msg or not msg.text:
            continue
        if str(msg.chat_id) != str(CHAT_ID):
            continue

        cmd = msg.text.strip().lower().split()[0]

        if cmd in ("/scan", "/quet", "/quét"):
            await _send(bot, f"🔄 Đang quét `{len(_symbols) or TOP_N}` tokens…")
            await run_scan(bot)

        elif cmd in ("/status", "/ping"):
            await _send(
                bot,
                f"✅ *Bot đang chạy*\n"
                f"📡 Interval: `{SCAN_MINS}` phút\n"
                f"📊 Tokens: `{len(_symbols)}`\n"
                f"⚡ Concurrency: `{CONCURRENCY}` luồng\n"
                f"🔄 Scan count: `{_scan_count}`\n"
                f"🕐 {_now_vn()} (GMT+7)",
            )

        elif cmd in ("/top", "/coins", "/list"):
            arg = msg.text.strip().split()
            n   = int(arg[1]) if len(arg) > 1 and arg[1].isdigit() else 30
            n   = min(n, 100)
            if _symbols:
                lines = [f"📋 *Top {n} / {len(_symbols)} tokens đang quét:*"]
                for i, s in enumerate(_symbols[:n], 1):
                    lines.append(f"  {i:>3}. `{s}`")
                await _send(bot, "\n".join(lines))
            else:
                await _send(bot, "⏳ Chưa có danh sách — đang chờ lần quét đầu")

        elif cmd in ("/refresh", "/update"):
            exchange = make_exchange()
            await _send(bot, f"🔄 Đang cập nhật top-{TOP_N} tokens…")
            await _refresh_symbols(exchange)
            await _send(bot, f"✅ Đã cập nhật: `{len(_symbols)}` tokens")

        elif cmd in ("/help", "/start"):
            await _send(
                bot,
                "🤖 *SMA25 + Smart Trail Bot v4 — 500 Tokens*\n\n"
                "Lệnh hỗ trợ:\n"
                "`/scan`     — Quét ngay lập tức\n"
                "`/status`   — Trạng thái bot\n"
                "`/top [N]`  — Xem top N coins đang quét\n"
                "`/refresh`  — Cập nhật lại danh sách tokens\n"
                "`/help`     — Hướng dẫn\n\n"
                f"⏱ Tự động quét mỗi `{SCAN_MINS}` phút\n"
                f"📊 Quét `{TOP_N}` tokens top volume 24h\n"
                "📡 Tín hiệu: SMA25 + ATR Trail + SQZ 1H",
            )
    return offset


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("Initialising bot…")
    bot = Bot(token=BOT_TOKEN)

    try:
        await bot.set_my_commands([
            BotCommand("scan",    "Quét tín hiệu ngay lập tức"),
            BotCommand("status",  "Trạng thái bot"),
            BotCommand("top",     "Top N coins đang quét"),
            BotCommand("refresh", "Cập nhật danh sách tokens"),
            BotCommand("help",    "Hướng dẫn"),
        ])
    except TelegramError:
        pass

    await _send(
        bot,
        f"🤖 *SMA25 + Smart Trail Bot v4 — Khởi động*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Top tokens:   `{TOP_N}` (by 24h volume)\n"
        f"⏱ Interval:     `{SCAN_MINS}` phút\n"
        f"⚡ Concurrency:  `{CONCURRENCY}` luồng\n"
        f"💰 Min volume:   `${MIN_VOL_M:.1f}M` / ngày\n"
        f"📈 Market:       `{MARKET_TYPE.upper()}`\n"
        f"📡 Timeframe:    `1H`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Bot sẵn sàng!  Gõ /help để xem lệnh",
    )

    scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")
    scheduler.add_job(
        run_scan, "interval",
        minutes=SCAN_MINS,
        args=[bot],
        next_run_time=datetime.now(),   # run immediately
        id="scan_job",
    )
    scheduler.start()
    logger.info(f"Scheduler started — every {SCAN_MINS} min")

    offset = 0
    while True:
        try:
            offset = await handle_updates(bot, offset)
        except TelegramError as e:
            logger.warning(f"Update polling: {e}")
        except Exception as e:
            logger.exception(f"Update loop error: {e}")
        await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
