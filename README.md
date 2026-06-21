# Telegram Caption Bot

A production-ready Telegram bot that processes channel photo posts and automatically appends the original post link to captions. Built with **Python 3.11+** and **Pyrogram**.

---

## Features

| Feature | Detail |
|---|---|
| `/single POST_LINK` | Process one post by Telegram URL |
| `/range START END` | Batch-process a range of message IDs |
| Album support | Processes photo-only albums; skips mixed albums |
| Re-upload (no forward) | Downloads then re-uploads — no Telegram "forwarded from" tags |
| Caption limit protection | Truncates safely; always keeps the link |
| FloodWait handling | Auto-sleeps and retries |
| Progress updates | Live Telegram status messages during `/range` |
| Processing report | Summary after every `/range` run |

---

## Caption Format

```
Original caption text here

----------------
🔗 Original Post:
https://t.me/c/CHANNEL_ID/MESSAGE_ID
```

If there is no original caption:

```
🔗 Original Post:
https://t.me/c/CHANNEL_ID/MESSAGE_ID
```

---

## Project Structure

```
telegram-caption-bot/
├── main.py          # Entry point
├── bot.py           # Pyrogram client + logging setup
├── handlers.py      # /start /help /status /single /range
├── utils.py         # Link generation, caption building, media checks
├── config.py        # Environment variable loading & validation
├── requirements.txt
├── render.yaml      # Render.com deployment spec
├── .env.example     # Template for environment variables
└── README.md
```

---

## Local Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/telegram-caption-bot.git
cd telegram-caption-bot
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Then edit .env with your values
```

### 4. Obtain credentials

| Variable | Where to get it |
|---|---|
| `API_ID` / `API_HASH` | https://my.telegram.org/apps |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `OWNER_ID` | [@userinfobot](https://t.me/userinfobot) |
| `SOURCE_CHANNEL` | Channel ID or username |
| `DESTINATION_CHANNEL` | Channel ID or username |

> **Important:** Add your bot as an **admin** to both the source and destination channels.

### 5. Run locally

```bash
python main.py
```

---

## GitHub Setup

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/telegram-caption-bot.git
git push -u origin main
```

> **Never commit `.env`** — it is already in `.gitignore`.

---

## Render.com Deployment

### Step 1 — Create a new Web Service

1. Go to [render.com](https://render.com) → **New** → **Background Worker**.
2. Connect your GitHub account and select the repo.

### Step 2 — Configure the service

| Field | Value |
|---|---|
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Plan** | Free (or Starter for 24/7 uptime) |

### Step 3 — Add environment variables

In the Render dashboard → **Environment** tab, add:

```
API_ID          = <your value>
API_HASH        = <your value>
BOT_TOKEN       = <your value>
OWNER_ID        = <your value>
SOURCE_CHANNEL  = <your value>
DESTINATION_CHANNEL = <your value>
```

### Step 4 — Deploy

Click **Deploy** (or push to `main` — Render auto-deploys on every push).

> **Session persistence:** Render's free tier does not have a persistent disk.  
> On the **Starter** plan or higher, attach a **Disk** at `/opt/render/project/src` so the Pyrogram session file survives restarts.  
> Alternatively, set `SESSION_STRING` via environment variable (see advanced section below).

---

## Channel ID Format

| Channel type | Format |
|---|---|
| Public | `@channelusername` or `channelusername` |
| Private | `-1001234567890` (full negative Telegram ID) |

To find a private channel's ID:
1. Forward any message from the channel to [@userinfobot](https://t.me/userinfobot).
2. It will display the channel's numeric ID.

---

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` | Show usage guide |
| `/status` | Show bot status and today's stats |
| `/single URL` | Process a single post |
| `/range START END` | Process a range of message IDs |

---

## What Gets Processed vs Skipped

| Media type | Action |
|---|---|
| 📷 Photo | ✅ Processed |
| 📷📷 Photo album (all photos) | ✅ Processed |
| 🎥 Video | ⏭ Skipped |
| 📄 Document / PDF | ⏭ Skipped |
| 🎵 Audio | ⏭ Skipped |
| 🎤 Voice note | ⏭ Skipped |
| 🎞 GIF / Animation | ⏭ Skipped |
| 🖼 Sticker | ⏭ Skipped |
| 📊 Poll | ⏭ Skipped |
| 📍 Location / Contact / Game | ⏭ Skipped |
| Mixed album (photo + any other) | ⏭ Entire album skipped |

---

## License

MIT
