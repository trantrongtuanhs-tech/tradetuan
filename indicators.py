"""
indicators.py — Technical indicator calculations
Matches Pine Script: SMA25 + Smart Trail Bot [Enhanced v4 - Tuan]
"""

import numpy as np
import pandas as pd
from typing import Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Core primitives
# ─────────────────────────────────────────────────────────────────────────────

def calc_sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).mean()


def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=length, min_periods=length).mean()


def calc_rsi(close: pd.Series, length: int) -> pd.Series:
    delta    = close.diff(1)
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ─────────────────────────────────────────────────────────────────────────────
# Smart Trail (ATR Trailing Stop) — matches Pine f_trail()
# ─────────────────────────────────────────────────────────────────────────────

def calc_smart_trail(
    close: pd.Series, high: pd.Series, low: pd.Series,
    atr_len: int = 10, atr_mult: float = 3.0
) -> Tuple[pd.Series, pd.Series]:
    """
    Returns (trail_price, direction)  direction: +1 BULL / -1 BEAR
    """
    atr_vals  = calc_atr(high, low, close, atr_len)
    stop_dist = atr_mult * atr_vals

    closes = close.values
    stops  = stop_dist.values

    trail     = np.full(len(closes), np.nan)
    direction = np.zeros(len(closes), dtype=int)

    t = float(closes[0])
    d = 1

    for i in range(len(closes)):
        c = closes[i]
        s = stops[i]
        if np.isnan(c) or np.isnan(s):
            trail[i]     = t
            direction[i] = d
            continue
        if c > t:
            t = max(t, c - s)
            d = 1
        elif c < t:
            t = min(t, c + s)
            d = -1
        trail[i]     = t
        direction[i] = d

    return (pd.Series(trail, index=close.index),
            pd.Series(direction, index=close.index))


# ─────────────────────────────────────────────────────────────────────────────
# Squeeze Momentum — matches Pine f_sqz_full()
# ─────────────────────────────────────────────────────────────────────────────

def calc_squeeze_momentum(
    close: pd.Series, high: pd.Series, low: pd.Series,
    bb_len: int = 20, bb_mult: float = 2.0,
    kc_len: int = 20, kc_mult: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Returns: (scaled_val, bull, rising, sqz_on, sqz_off)
    """
    # Bollinger Bands
    bb_basis  = calc_sma(close, bb_len)
    bb_std    = close.rolling(bb_len).std() * bb_mult
    upper_bb  = bb_basis + bb_std
    lower_bb  = bb_basis - bb_std

    # Keltner Channels (simple range, matching Pine Script)
    kc_ma     = calc_sma(close, kc_len)
    rng       = high - low                       # Pine: high-low (not TR)
    range_ma  = calc_sma(rng, kc_len)
    upper_kc  = kc_ma + range_ma * kc_mult
    lower_kc  = kc_ma - range_ma * kc_mult

    sqz_on  = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    sqz_off = (lower_bb < lower_kc) & (upper_bb > upper_kc)

    # Momentum value: linreg of delta
    highest = high.rolling(kc_len).max()
    lowest  = low.rolling(kc_len).min()
    delta   = close - (((highest + lowest) / 2) + calc_sma(close, kc_len)) / 2

    def _linreg_last(arr: np.ndarray) -> float:
        n = len(arr)
        if n < 2 or np.all(np.isnan(arr)):
            return np.nan
        idx    = np.arange(n, dtype=float)
        coeffs = np.polyfit(idx, arr, 1)
        return coeffs[0] * (n - 1) + coeffs[1]

    val = delta.rolling(kc_len).apply(_linreg_last, raw=True)

    # ATR-normalised scaling (×5)
    atr14  = calc_atr(high, low, close, 14)
    scaled = val / atr14.replace(0, np.nan) * 5.0

    bull   = val > 0
    rising = val > val.shift(1)

    return scaled, bull, rising, sqz_on, sqz_off


# ─────────────────────────────────────────────────────────────────────────────
# MACD — matches Pine ta.macd()
# ─────────────────────────────────────────────────────────────────────────────

def calc_macd(
    close: pd.Series,
    fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast    = calc_ema(close, fast)
    ema_slow    = calc_ema(close, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    hist        = macd_line - signal_line
    return macd_line, signal_line, hist


# ─────────────────────────────────────────────────────────────────────────────
# DMI / ADX — matches Pine ta.dmi()
# ─────────────────────────────────────────────────────────────────────────────

def calc_dmi_adx(
    high: pd.Series, low: pd.Series, close: pd.Series,
    length: int = 14
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (plus_di, minus_di, adx)"""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    up   = high.diff(1)
    down = -low.diff(1)

    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)

    pdm_s = pd.Series(pdm, index=high.index)
    mdm_s = pd.Series(mdm, index=high.index)

    atr_s   = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * pdm_s.ewm(alpha=1 / length, adjust=False).mean() / atr_s
    minus_di= 100 * mdm_s.ewm(alpha=1 / length, adjust=False).mean() / atr_s

    dx_num  = (plus_di - minus_di).abs()
    dx_den  = (plus_di + minus_di).replace(0, np.nan)
    dx      = 100 * dx_num / dx_den
    adx_val = dx.ewm(alpha=1 / length, adjust=False).mean()

    return plus_di, minus_di, adx_val
