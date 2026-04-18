"""
formatter.py — Format scan results into Telegram messages (Vietnamese UI)
"""

from datetime import datetime


SIGNAL_EMOJI = {
    "CONFIRMED BUY":  "🚀",
    "CONFIRMED SELL": "💀",
    "WEAK BUY":       "📈",
    "WEAK SELL":      "📉",
}

VERDICT_EMOJI = {
    "STRONG BUY":  "🟢🟢",
    "BUY":         "🟢",
    "Flip BUY":    "🔼",
    "STRONG SELL": "🔴🔴",
    "SELL":        "🔴",
    "Flip SELL":   "🔽",
    "HOLD LONG":   "🔵",
    "HOLD SHORT":  "🟠",
}


def _now_vn() -> str:
    from datetime import timezone, timedelta
    tz_vn = timezone(timedelta(hours=7))
    return datetime.now(tz_vn).strftime("%H:%M:%S  %d/%m/%Y")


def _price_fmt(p: float) -> str:
    """Auto format price: many decimals for low-priced coins."""
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.6f}"


# ─────────────────────────────────────────────────────────────────────────────
# Summary line (used in scan-summary message)
# ─────────────────────────────────────────────────────────────────────────────

def format_summary_line(r: dict) -> str:
    emoji = SIGNAL_EMOJI.get(r["signal"], "📊")
    v_emoji = VERDICT_EMOJI.get(r["sqz_verdict"], "")
    confirmed = "✅" if "CONFIRMED" in r["signal"] else "⚠️"
    return (
        f"{emoji} `{r['symbol']}` {confirmed} {r['signal']}\n"
        f"   💰 {_price_fmt(r['price'])}  |  RSI {r['rsi']}  |  ADX {r['adx']}"
        f"{'⚡' if r['adx_strong'] else ''}  |  SQZ {v_emoji} {r['sqz_verdict']}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Full detail card per signal
# ─────────────────────────────────────────────────────────────────────────────

def format_signal_card(r: dict) -> str:
    emoji   = SIGNAL_EMOJI.get(r["signal"], "📊")
    sqz_dot = "🔴" if r["sqz_on"] else "⚪"
    sqz_dir = "▲ BULL" if r["sqz_bull"] else "▼ BEAR"
    sqz_arr = "↑ rising" if r["sqz_rising"] else "↓ falling"
    v_emoji = VERDICT_EMOJI.get(r["sqz_verdict"], "")

    rr = round(abs(r["tp"] - r["entry"]) / max(abs(r["entry"] - r["sl"]), 1e-9), 1)

    be_tag = ""
    lines = [
        f"{emoji} *{r['signal']}*  —  `{r['symbol']}`",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Giá:      `{_price_fmt(r['price'])}`",
        f"📏 SMA 25:   `{_price_fmt(r['sma25'])}`",
        f"🎯 Trail:    `{_price_fmt(r['trail_price'])}`  {r['trail_dir']}",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 *Indicators 1H*",
        f"  RSI:    `{r['rsi']}`",
        f"  MACD:   {r['macd']}",
        f"  Trend:  {r['trend']}",
        f"  EMA200: {r['above_200']}",
        f"  ADX:    `{r['adx']}` {'⚡ Strong' if r['adx_strong'] else '〰 Weak'} {r['adx_dir']}",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"🌀 *Squeeze Momentum*",
        f"  Status:  {sqz_dot} {'SQZ ON 🔒' if r['sqz_on'] else 'SQZ OFF'}",
        f"  Hướng:   `{sqz_dir}`  {sqz_arr}",
        f"  Verdict: {v_emoji} `{r['sqz_verdict']}`",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 Vol: {'✅ OK' if r['vol_ok'] else '❌ Thấp'}",
        f"📈 Score BUY {r['cb']}/4  |  SELL {r['cs']}/4",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"🛡 *Risk Management (1H)*",
        f"  ⚡ Entry: `{_price_fmt(r['entry'])}`",
        f"  🔴 SL:    `{_price_fmt(r['sl'])}`",
        f"  🟢 TP:    `{_price_fmt(r['tp'])}`",
        f"  📐 RR:    `{rr}:1`",
        f"  ATR:     `{_price_fmt(r['atr_1h'])}`",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏁 {r['overall']}",
        f"🕐 {_now_vn()} (GMT+7)",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Scan summary header
# ─────────────────────────────────────────────────────────────────────────────

def format_scan_header(results: list[dict], total_symbols: int, interval_min: int) -> str:
    confirmed = sum(1 for r in results if "CONFIRMED" in r["signal"])
    weak      = len(results) - confirmed
    buy_c     = sum(1 for r in results if "BUY"  in r["signal"] and "CONFIRMED" in r["signal"])
    sell_c    = sum(1 for r in results if "SELL" in r["signal"] and "CONFIRMED" in r["signal"])

    lines = [
        f"📡 *SCAN 1H — {_now_vn()}*",
        f"🔍 Quét `{total_symbols}` coins  |  ⏱ mỗi `{interval_min}` phút",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Kết quả:  `{len(results)}` tín hiệu",
        f"  ✅ Confirmed: `{confirmed}`  (🟢 BUY {buy_c}  /  🔴 SELL {sell_c})",
        f"  ⚠️  Weak:     `{weak}`",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not results:
        lines.append("😴 Không có tín hiệu trong lần quét này")
    else:
        for r in results:
            lines.append(format_summary_line(r))
    return "\n".join(lines)


def format_no_signal_message(total: int) -> str:
    return (
        f"😴 *Không có tín hiệu*\n"
        f"Đã quét `{total}` coins trên 1H\n"
        f"🕐 {_now_vn()} (GMT+7)"
    )
