"""
scanner.py — Fetch 1H data from Binance + calculate signals
Logic mirrors Pine Script: SMA25 + Smart Trail Bot [Enhanced v4 - Tuan]
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
# Default params (match Pine Script grp7 defaults)
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
    rsi_pullback = 45,
    rsi_pushback = 55,
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
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sqz_verdict(
    bull_cur: bool, bull_prev: bool,
    rise_cur: bool,
    cb: int, cs: int,
    adx_strong: bool, adx_bull: bool, adx_bear: bool,
) -> str:
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


def _overall(bc: int, sc: int) -> str:
    if bc >= 7: return f"🚀 STRONG BUY [{bc}]"
    if sc >= 7: return f"💀 STRONG SELL [{sc}]"
    if bc >= 5: return f"📈 BUY [{bc}]"
    if sc >= 5: return f"📉 SELL [{sc}]"
    if bc >= 4: return f"🔼 LEAN BUY [{bc}]"
    if sc >= 4: return f"🔽 LEAN SELL [{sc}]"
    return "⚖ NEUTRAL"


# ─────────────────────────────────────────────────────────────────────────────
# Main analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze_symbol(exchange: ccxt.Exchange, symbol: str) -> Optional[dict]:
    """
    Fetch 1H OHLCV for *symbol* and return signal dict, or None if no signal.
    """
    try:
        raw = exchange.fetch_ohlcv(symbol, '1h', limit=CFG['bars_needed'])
        if len(raw) < 150:
            logger.warning(f"{symbol}: not enough bars ({len(raw)})")
            return None

        df = pd.DataFrame(raw, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        df.set_index('ts', inplace=True)

        o, h, l, c, v = df['open'], df['high'], df['low'], df['close'], df['volume']

        # ── SMA 25 ───────────────────────────────────────────────────────
        sma25     = calc_sma(c, CFG['sma_len'])
        sma_sig   = 1 if c.iloc[-1] >= sma25.iloc[-1] else -1
        sma_sig_p = 1 if c.iloc[-2] >= sma25.iloc[-2] else -1

        # ── Smart Trail ──────────────────────────────────────────────────
        trail_v, trail_d = calc_smart_trail(c, h, l, CFG['atr_len'], CFG['atr_mult'])
        trail_sig   = int(trail_d.iloc[-1])
        trail_sig_p = int(trail_d.iloc[-2])

        # ── ATR (1H) ─────────────────────────────────────────────────────
        atr_s   = calc_atr(h, l, c, CFG['atr_len'])
        atr_1h  = float(atr_s.iloc[-1])

        # ── Volume filter ─────────────────────────────────────────────────
        vol_ma = calc_sma(v, CFG['vol_len'])
        vol_ok = bool(v.iloc[-1] > vol_ma.iloc[-1])

        # ── RSI filter ───────────────────────────────────────────────────
        rsi_ser  = calc_rsi(c, CFG['rsi_len'])
        rsi_cur  = float(rsi_ser.iloc[-1])
        rsi_buy_ok  = rsi_cur < CFG['rsi_ob']
        rsi_sell_ok = rsi_cur > CFG['rsi_os']

        # ── Confluence signal (conf_only=True) ───────────────────────────
        bull   = sma_sig > 0 and trail_sig > 0
        bear   = sma_sig < 0 and trail_sig < 0
        prev_bull = sma_sig_p > 0 and trail_sig_p > 0
        prev_bear = sma_sig_p < 0 and trail_sig_p < 0

        base_buy  = bull and not prev_bull
        base_sell = bear and not prev_bear

        # ── EMA / trend ──────────────────────────────────────────────────
        ema_f   = calc_ema(c, CFG['ema_fast'])
        ema_s   = calc_ema(c, CFG['ema_slow'])
        ema_200 = calc_ema(c, CFG['ema_200'])

        trend_up   = bool(ema_f.iloc[-1] > ema_s.iloc[-1])
        above_200  = bool(c.iloc[-1] > ema_200.iloc[-1])

        # ── RSI score (Script 2) ─────────────────────────────────────────
        rsi_s2      = calc_rsi(c, CFG['rsi_len'])
        rsi_ema_f   = calc_ema(rsi_s2, CFG['rsi_ema_fast'])
        rsi_ema_s   = calc_ema(rsi_s2, CFG['rsi_ema_slow'])
        rsi_ema_bull = bool(rsi_ema_f.iloc[-1] > rsi_ema_s.iloc[-1])
        rsi_ema_bear = bool(rsi_ema_f.iloc[-1] < rsi_ema_s.iloc[-1])

        # ── MACD ─────────────────────────────────────────────────────────
        _, _, macd_hist = calc_macd(c, CFG['macd_fast'], CFG['macd_slow'], CFG['macd_sig'])
        macd_bull = bool(macd_hist.iloc[-1] > 0)
        macd_bear = bool(macd_hist.iloc[-1] < 0)

        # ── ADX / DMI ────────────────────────────────────────────────────
        plus_di, minus_di, adx_v = calc_dmi_adx(h, l, c, CFG['adx_len'])
        adx_val      = float(adx_v.iloc[-1])
        adx_strong   = adx_val > CFG['adx_thresh']
        adx_bull     = bool(plus_di.iloc[-1] > minus_di.iloc[-1])
        adx_bear     = bool(minus_di.iloc[-1] > plus_di.iloc[-1])

        # ── Squeeze Momentum ─────────────────────────────────────────────
        _, sqz_bull, sqz_rise, sqz_on, _ = calc_squeeze_momentum(
            c, h, l,
            CFG['sqz_bb_len'], CFG['sqz_bb_mult'],
            CFG['sqz_kc_len'], CFG['sqz_kc_mult'],
        )
        sqz_bull_cur  = bool(sqz_bull.iloc[-1])
        sqz_bull_prev = bool(sqz_bull.iloc[-2])
        sqz_rise_cur  = bool(sqz_rise.iloc[-1])
        sqz_on_cur    = bool(sqz_on.iloc[-1])

        # ── S2 Confirm (matches Pine s2_confirm_buy/sell) ────────────────
        s2_confirm_buy  = sqz_bull_cur and (trend_up or above_200) and (rsi_ema_bull or macd_bull)
        s2_confirm_sell = (not sqz_bull_cur
                           and (not trend_up or not above_200)
                           and (rsi_ema_bear or macd_bear))

        # ── Final signals ────────────────────────────────────────────────
        final_buy  = base_buy  and vol_ok and rsi_buy_ok  and s2_confirm_buy
        final_sell = base_sell and vol_ok and rsi_sell_ok and s2_confirm_sell
        weak_buy   = base_buy  and vol_ok and rsi_buy_ok  and not s2_confirm_buy
        weak_sell  = base_sell and vol_ok and rsi_sell_ok and not s2_confirm_sell

        if not (final_buy or final_sell or weak_buy or weak_sell):
            return None

        signal = (
            "CONFIRMED BUY"  if final_buy  else
            "CONFIRMED SELL" if final_sell else
            "WEAK BUY"       if weak_buy   else
            "WEAK SELL"
        )

        # ── SL / TP ──────────────────────────────────────────────────────
        entry = float(c.iloc[-1])
        if final_buy or weak_buy:
            sl = entry - CFG['sl_atr_mult'] * atr_1h
            tp = entry + (entry - sl) * CFG['rr_ratio']
        else:
            sl = entry + CFG['sl_atr_mult'] * atr_1h
            tp = entry - (sl - entry) * CFG['rr_ratio']

        # ── Buy/sell score (Overall) ──────────────────────────────────────
        bc = (
            (1 if sma_sig > 0 and trail_sig > 0 else 0) +
            (2 if sma_sig > 0 and trail_sig > 0 else 0) +   # 1H weight x2
            (1 if sqz_bull_cur and trend_up else 0) +
            (1 if macd_bull else 0)
        )
        sc = (
            (1 if sma_sig < 0 and trail_sig < 0 else 0) +
            (2 if sma_sig < 0 and trail_sig < 0 else 0) +
            (1 if not sqz_bull_cur and not trend_up else 0) +
            (1 if macd_bear else 0)
        )

        # ── SQZ Verdict ───────────────────────────────────────────────────
        cb = sum([rsi_ema_bull, trend_up, macd_bull, above_200])
        cs = sum([rsi_ema_bear, not trend_up, macd_bear, not above_200])

        verdict = _sqz_verdict(
            sqz_bull_cur, sqz_bull_prev, sqz_rise_cur,
            cb, cs, adx_strong, adx_bull, adx_bear
        )

        return {
            "symbol":       symbol,
            "signal":       signal,
            "price":        entry,
            "sma25":        float(sma25.iloc[-1]),
            "trail_price":  float(trail_v.iloc[-1]),
            "trail_dir":    "▲ BULL" if trail_sig > 0 else "▼ BEAR",
            "rsi":          round(rsi_cur, 1),
            "rsi_ema_bull": rsi_ema_bull,
            "macd":         "▲ BULL" if macd_bull else "▼ BEAR",
            "trend":        "▲ UP" if trend_up else "▼ DOWN",
            "above_200":    "▲ Above" if above_200 else "▼ Below",
            "adx":          round(adx_val, 1),
            "adx_strong":   adx_strong,
            "adx_dir":      "▲" if adx_bull else "▼",
            "sqz_on":       sqz_on_cur,
            "sqz_bull":     sqz_bull_cur,
            "sqz_rising":   sqz_rise_cur,
            "sqz_verdict":  verdict,
            "vol_ok":       vol_ok,
            "entry":        round(entry, 6),
            "sl":           round(sl, 6),
            "tp":           round(tp, 6),
            "atr_1h":       round(atr_1h, 6),
            "overall":      _overall(bc, sc),
            "cb":           cb,
            "cs":           cs,
        }

    except ccxt.NetworkError as e:
        logger.error(f"{symbol} network error: {e}")
    except ccxt.ExchangeError as e:
        logger.error(f"{symbol} exchange error: {e}")
    except Exception as e:
        logger.exception(f"{symbol} unexpected error: {e}")

    return None


async def scan_symbols_async(
    exchange: ccxt.Exchange,
    symbols: list[str],
    delay: float = 0.4,
) -> list[dict]:
    """Run analysis for all symbols with rate-limit delay, returns list of signal dicts."""
    results = []
    for sym in symbols:
        result = await asyncio.to_thread(analyze_symbol, exchange, sym)
        if result:
            results.append(result)
        await asyncio.sleep(delay)

    # Sort: CONFIRMED first, then by signal type
    priority = {"CONFIRMED BUY": 0, "CONFIRMED SELL": 1, "WEAK BUY": 2, "WEAK SELL": 3}
    results.sort(key=lambda x: priority.get(x["signal"], 99))
    return results
