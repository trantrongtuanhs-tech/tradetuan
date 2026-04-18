# 🤖 SMA25 + Smart Trail Bot v4 — Telegram Scanner

Bot tự động quét tín hiệu **khung 1H** trên Binance, dựa trên Pine Script  
`SMA25 + Smart Trail Bot [Enhanced v4 - Tuan]`.

---

## 📐 Logic tín hiệu (mirror từ Pine Script)

| Điều kiện | Mô tả |
|-----------|-------|
| **SMA 25** | Giá trên/dưới SMA 25 |
| **Smart Trail (ATR)** | ATR-based trailing stop (len=10, mult=3.0) |
| **Confluence** | SMA + Trail cùng chiều → crossover = tín hiệu |
| **Volume Filter** | Volume > SMA(Volume, 20) |
| **RSI Filter** | RSI < 70 (buy) / RSI > 30 (sell) |
| **S2 Confirm (1H)** | SQZ bull + EMA trend + RSI EMA cross + MACD |
| **CONFIRMED** | Tất cả điều kiện thỏa |
| **WEAK** | Base signal OK nhưng thiếu S2 confirm |

---

## ⚙️ Cài đặt nhanh

### 1. Clone repo & tạo bot Telegram

```bash
git clone https://github.com/your-username/sma25-bot.git
cd sma25-bot
```

Tạo bot qua [@BotFather](https://t.me/BotFather) → lấy `BOT_TOKEN`  
Lấy `CHAT_ID` của bạn qua [@userinfobot](https://t.me/userinfobot)

### 2. Chạy local

```bash
pip install -r requirements.txt
cp .env.example .env
# Điền BOT_TOKEN và CHAT_ID vào .env
python main.py
```

### 3. Deploy lên Railway

1. Push code lên GitHub
2. Vào [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
3. Chọn repo → Railway tự detect `Procfile`
4. Vào **Variables** → thêm các biến sau:

| Variable | Giá trị |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Token từ BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID của bạn |
| `SCAN_INTERVAL_MINUTES` | `60` (mặc định) |
| `MARKET_TYPE` | `spot` hoặc `future` |
| `SEND_WEAK_SIGNALS` | `true` hoặc `false` |
| `TOP_COINS` | Danh sách coins cách nhau bằng dấu phẩy |

5. Click **Deploy** → Bot chạy 24/7 miễn phí trên Railway Hobby plan

---

## 📱 Lệnh Telegram

| Lệnh | Mô tả |
|------|-------|
| `/scan` | Quét ngay lập tức (không cần chờ interval) |
| `/status` | Kiểm tra bot đang chạy |
| `/list` | Xem danh sách coins đang quét |
| `/help` | Hướng dẫn |

---

## 📊 Ví dụ tin nhắn signal

```
🚀 CONFIRMED BUY — BTC/USDT
━━━━━━━━━━━━━━━━━━━━━━━
💰 Giá:      65,432.1200
📏 SMA 25:   64,100.5000
🎯 Trail:    63,800.0000  ▲ BULL
━━━━━━━━━━━━━━━━━━━━━━━
📊 Indicators 1H
  RSI:    48.3
  MACD:   ▲ BULL
  Trend:  ▲ UP
  EMA200: ▲ Above
  ADX:    32.1 ⚡ Strong ▲
━━━━━━━━━━━━━━━━━━━━━━━
🌀 Squeeze Momentum
  Status:  ⚪ SQZ OFF
  Hướng:   ▲ BULL  ↑ rising
  Verdict: 🟢 BUY
━━━━━━━━━━━━━━━━━━━━━━━
🛡 Risk Management (1H)
  ⚡ Entry: 65,432.1200
  🔴 SL:    64,932.1200
  🟢 TP:    66,432.1200
  📐 RR:    2.0:1
```

---

## 📁 Cấu trúc file

```
sma25_bot/
├── main.py          ← Bot chính + scheduler + Telegram commands
├── scanner.py       ← Fetch Binance + tính toán tất cả indicators
├── indicators.py    ← SMA, EMA, ATR, RSI, Smart Trail, SQZ, MACD, ADX
├── formatter.py     ← Format tin nhắn Telegram (tiếng Việt)
├── requirements.txt
├── Procfile         ← Railway deployment
├── railway.json     ← Railway config
└── .env.example     ← Mẫu biến môi trường
```

---

## 🔧 Tùy chỉnh thêm

Để thay đổi thông số indicator, chỉnh `CFG` dict trong `scanner.py`:

```python
CFG = dict(
    sma_len   = 25,    # SMA length
    atr_len   = 10,    # ATR length
    atr_mult  = 3.0,   # ATR multiplier
    rr_ratio  = 2.0,   # Risk:Reward
    ...
)
```
