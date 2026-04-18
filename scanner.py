"""
scanner.py — Fetch 1H data from Binance + calculate signals
Logic mirrors Pine Script: SMA25 + Smart Trail Bot [Enhanced v4 - Tuan]

Extended: auto-fetch top N tokens by 24h USDT volume.
Concurrent scanning via semaphore to handle 500 symbols efficiently.
"""

import asyncio
import logging
from typing import Optional

import ccxt
import numpy as np
import pandas as pd

from indicators import (
    calc_sma, calc_ema, calc_atr, calc_rsi,
    calc_smart_trail, calc_squeeze_momentum,
    calc_macd, calc_dmi_adx,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Indicator params  (match Pine Script grp7 defaults)
# ─────────────────────────────────────────────────────────────────────────────
CFG = dict(
    sma_len      = 25,
    atr_len      = 10,
    atr_mult     = 3.0,
    vol_len      = 20,
    rsi_len      = 14,
    rsi_ob       = 70,
    rsi_os       = 30,
    ema_fast     = 21,
    ema_slow     = 50,
    ema_200      = 200,
    rsi_ema_fast = 21,
    rsi_ema_slow = 49,
    adx_len      = 14,
    adx_thresh   = 25,
    macd_fast    = 12,
    macd_slow    = 26,
    macd_sig     = 9,
    sqz_bb_len   = 20,
    sqz_bb_mult  = 2.0,
    sqz_kc_len   = 20,
    sqz_kc_mult  = 1.5,
    sl_atr_mult  = 1.0,
    rr_ratio     = 2.0,
    bars_needed  = 300,
)

# ─────────────────────────────────────────────────────────────────────────────
# Blacklist — stablecoins, wrapped tokens, leveraged tokens
# ─────────────────────────────────────────────────────────────────────────────
_BL = frozenset([
    "USDC", "BUSD", "TUSD", "USDP", "DAI", "FDUSD", "USDD",
    "PAXG", "WBTC", "WETH", "WBNB",
])
_BL_SUFFIX = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S")

def _is_blacklisted(symbol: str) -> bool:
    base = symbol.split("/")[0]
    if base in _BL:
        return True
    for sfx in _BL_SUFFIX:
        if base.endswith(sfx):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic symbol list — top N by 24h quote volume
# ─────────────────────────────────────────────────────────────────────────────

def get_top_symbols(
    exchange: ccxt.Exchange,
    quote: str = "USDT",
    top_n: int = 500,
    min_volume_usdt: float = 500_000,
) -> list:
    """
    Single fetch_tickers() call → rank all USDT pairs by 24h quoteVolume
    → return top_n symbols.
    """
    logger.info(f"Fetching tickers to build top-{top_n} {quote} list…")
    try:
        tickers = exchange.fetch_tickers()
    except Exception as e:
        logger.error(f"fetch_tickers failed: {e}")
        return []

    rows = []
    for sym, tk in tickers.items():
        if not sym.endswith(f"/{quote}"):
            continue
        if _is_blacklisted(sym):
            continue
        qvol = tk.get("quoteVolume") or 0
        if qvol < min_volume_usdt:
            continue
        rows.append((sym, float(qvol)))

    rows.sort(key=lambda x: x[1], reverse=True)
    symbols = [r[0] for r in rows[:top_n]]

    vol_top = rows[0][1] / 1e6 if rows else 0
    vol_bot = rows[min(top_n - 1, len(rows) - 1)][1] / 1e6 if rows else 0
    logger.info(
        f"Symbol list: {len(symbols)} tokens | "
        f"vol range ${vol_bot:.1f}M – ${vol_top:.0f}M"
    )
    return symbols


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sqz_verdict(bull_cur, bull_prev, rise_cur, cb, cs, adx_strong, adx_bull, adx_bear):
    flip_up   = bull_cur  and not bull_prev
    flip_down = not bull_cur and bull_prev
    strong_buy  = flip_up   and cb >= 2 and (adx_strong and adx_bull or cb >= 3)
    strong_sell = flip_down and cs >= 2 and (adx_strong and adx_bear or cs >= 3)
    norm_buy    = bull_cur  and rise_cur  and cb >= 1 and not strong_buy
    norm_sell   = not bull_cur and not rise_cur and cs >= 1 and not strong_sell
    if strong_buy:   return "STRONG BUY"
    if norm_buy:     return "BUY"
    if flip_up:      return "Flip BUY"
    if strong_sell:  return "STRONG SELL"
    if norm_sell:    return "SELL"
    if flip_down:    return "Flip SELL"
    if bull_cur:     return "HOLD LONG"
    return "HOLD SHORT"

def _overall(bc, sc):
    if bc >= 7: return f"🚀 STRONG BUY [{bc}]"
    if sc >= 7: return f"💀 STRONG SELL [{sc}]"
    if bc >= 5: return f"📈 BUY [{bc}]"
    if sc >= 5: return f"📉 SELL [{sc}]"
    if bc >= 4: return f"🔼 LEAN BUY [{bc}]"
    if sc >= 4: return f"🔽 LEAN SELL [{sc}]"
    return "⚖ NEUTRAL"


# ─────────────────────────────────────────────────────────────────────────────
# Single-symbol analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze_symbol(exchange: ccxt.Exchange, symbol: str) -> Optional[dict]:
    try:
        raw = exchange.fetch_ohlcv(symbol, "1h", limit=CFG["bars_needed"])
        if len(raw) < 150:
            return None

        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)

        h, l, c, v = df["high"], df["low"], df["close"], df["volume"]

        # SMA 25
        sma25     = calc_sma(c, CFG["sma_len"])
        sma_sig   = 1 if c.iloc[-1] >= sma25.iloc[-1] else -1
        sma_sig_p = 1 if c.iloc[-2] >= sma25.iloc[-2] else -1

        # Smart Trail
        trail_v, trail_d = calc_smart_trail(c, h, l, CFG["atr_len"], CFG["atr_mult"])
        trail_sig   = int(trail_d.iloc[-1])
        trail_sig_p = int(trail_d.iloc[-2])

        # ATR
        atr_1h = float(calc_atr(h, l, c, CFG["atr_len"]).iloc[-1])

        # Volume filter
        vol_ma = calc_sma(v, CFG["vol_len"])
        vol_ok = bool(v.iloc[-1] > vol_ma.iloc[-1])

        # RSI filter
        rsi_ser     = calc_rsi(c, CFG["rsi_len"])
        rsi_cur     = float(rsi_ser.iloc[-1])
        rsi_buy_ok  = rsi_cur < CFG["rsi_ob"]
        rsi_sell_ok = rsi_cur > CFG["rsi_os"]

        # Confluence
        bull      = sma_sig > 0 and trail_sig > 0
        bear      = sma_sig < 0 and trail_sig < 0
        prev_bull = sma_sig_p > 0 and trail_sig_p > 0
        prev_bear = sma_sig_p < 0 and trail_sig_p < 0
        base_buy  = bull and not prev_bull
        base_sell = bear and not prev_bear

        # EMA trend
        ema_f     = calc_ema(c, CFG["ema_fast"])
        ema_s     = calc_ema(c, CFG["ema_slow"])
        ema_200   = calc_ema(c, CFG["ema_200"])
        trend_up  = bool(ema_f.iloc[-1] > ema_s.iloc[-1])
        above_200 = bool(c.iloc[-1] > ema_200.iloc[-1])

        # RSI EMA
        rsi_s2       = calc_rsi(c, CFG["rsi_len"])
        rsi_ema_f    = calc_ema(rsi_s2, CFG["rsi_ema_fast"])
        rsi_ema_s    = calc_ema(rsi_s2, CFG["rsi_ema_slow"])
        rsi_ema_bull = bool(rsi_ema_f.iloc[-1] > rsi_ema_s.iloc[-1])
        rsi_ema_bear = bool(rsi_ema_f.iloc[-1] < rsi_ema_s.iloc[-1])

        # MACD
        _, _, macd_hist = calc_macd(c, CFG["macd_fast"], CFG["macd_slow"], CFG["macd_sig"])
        macd_bull = bool(macd_hist.iloc[-1] > 0)
        macd_bear = bool(macd_hist.iloc[-1] < 0)

        # ADX
        plus_di, minus_di, adx_v = calc_dmi_adx(h, l, c, CFG["adx_len"])
        adx_val    = float(adx_v.iloc[-1])
        adx_strong = adx_val > CFG["adx_thresh"]
        adx_bull   = bool(plus_di.iloc[-1] > minus_di.iloc[-1])
        adx_bear   = bool(minus_di.iloc[-1] > plus_di.iloc[-1])

        # SQZ
        _, sqz_bull, sqz_rise, sqz_on, _ = calc_squeeze_momentum(
            c, h, l,
            CFG["sqz_bb_len"], CFG["sqz_bb_mult"],
            CFG["sqz_kc_len"], CFG["sqz_kc_mult"],
        )
        sqz_bc = bool(sqz_bull.iloc[-1])
        sqz_bp = bool(sqz_bull.iloc[-2])
        sqz_rc = bool(sqz_rise.iloc[-1])
        sqz_oc = bool(sqz_on.iloc[-1])

        # S2 confirm
        s2_buy  = sqz_bc and (trend_up or above_200) and (rsi_ema_bull or macd_bull)
        s2_sell = (not sqz_bc
                   and (not trend_up or not above_200)
                   and (rsi_ema_bear or macd_bear))

        final_buy  = base_buy  and vol_ok and rsi_buy_ok  and s2_buy
        final_sell = base_sell and vol_ok and rsi_sell_ok and s2_sell
        weak_buy   = base_buy  and vol_ok and rsi_buy_ok  and not s2_buy
        weak_sell  = base_sell and vol_ok and rsi_sell_ok and not s2_sell

        if not (final_buy or final_sell or weak_buy or weak_sell):
            return None

        signal = (
            "CONFIRMED BUY"  if final_buy  else
            "CONFIRMED SELL" if final_sell else
            "WEAK BUY"       if weak_buy   else
            "WEAK SELL"
        )

        entry = float(c.iloc[-1])
        if final_buy or weak_buy:
            sl = entry - CFG["sl_atr_mult"] * atr_1h
            tp = entry + (entry - sl) * CFG["rr_ratio"]
        else:
            sl = entry + CFG["sl_atr_mult"] * atr_1h
            tp = entry - (sl - entry) * CFG["rr_ratio"]

        cb = sum([rsi_ema_bull, trend_up, macd_bull, above_200])
        cs = sum([rsi_ema_bear, not trend_up, macd_bear, not above_200])
        bc = (1 if bull else 0) + (1 if sqz_bc and trend_up else 0) + (1 if macd_bull else 0)
        sc = (1 if bear else 0) + (1 if not sqz_bc and not trend_up else 0) + (1 if macd_bear else 0)

        return {
            "symbol":      symbol,
            "signal":      signal,
            "price":       entry,
            "sma25":       float(sma25.iloc[-1]),
            "trail_price": float(trail_v.iloc[-1]),
            "trail_dir":   "▲ BULL" if trail_sig > 0 else "▼ BEAR",
            "rsi":         round(rsi_cur, 1),
            "macd":        "▲ BULL" if macd_bull else "▼ BEAR",
            "trend":       "▲ UP"   if trend_up  else "▼ DOWN",
            "above_200":   "▲ Above" if above_200 else "▼ Below",
            "adx":         round(adx_val, 1),
            "adx_strong":  adx_strong,
            "adx_dir":     "▲" if adx_bull else "▼",
            "sqz_on":      sqz_oc,
            "sqz_bull":    sqz_bc,
            "sqz_rising":  sqz_rc,
            "sqz_verdict": _sqz_verdict(sqz_bc, sqz_bp, sqz_rc, cb, cs, adx_strong, adx_bull, adx_bear),
            "vol_ok":      vol_ok,
            "entry":       round(entry, 6),
            "sl":          round(sl, 6),
            "tp":          round(tp, 6),
            "atr_1h":      round(atr_1h, 6),
            "overall":     _overall(bc, sc),
            "cb":          cb,
            "cs":          cs,
        }

    except ccxt.NetworkError as e:
        logger.warning(f"{symbol} network: {e}")
    except ccxt.ExchangeError as e:
        logger.warning(f"{symbol} exchange: {e}")
    except Exception as e:
        logger.debug(f"{symbol} skipped: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Concurrent batch scanner
# ─────────────────────────────────────────────────────────────────────────────

async def scan_symbols_async(
    exchange: ccxt.Exchange,
    symbols: list,
    concurrency: int = 8,
    progress_cb=None,
) -> list:
    """
    Scan all symbols concurrently (max `concurrency` simultaneous requests).
    500 symbols @ 8 concurrent ≈ 2–4 minutes depending on latency.
    """
    semaphore  = asyncio.Semaphore(concurrency)
    results    = []
    done_count = 0
    total      = len(symbols)

    async def _worker(sym: str):
        nonlocal done_count
        async with semaphore:
            result = await asyncio.to_thread(analyze_symbol, exchange, sym)
            done_count += 1
            if progress_cb:
                try:
                    await progress_cb(done_count, total)
                except Exception:
                    pass
            if result:
                results.append(result)

    await asyncio.gather(*[_worker(s) for s in symbols])

    priority = {"CONFIRMED BUY": 0, "CONFIRMED SELL": 1, "WEAK BUY": 2, "WEAK SELL": 3}
    results.sort(key=lambda x: priority.get(x["signal"], 99))
    return results
