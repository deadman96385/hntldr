# hntldr 🧠

> HN without the useless titles.

Paste any Hacker News link and get a direct 1-2 sentence summary instead of vague clickbait. Auto-posts top stories to a Telegram channel with article + comments links.

**Example output:**
```
Show HN: AsteroidOS 2.0 - Nobody asked, we shipped anyway

AsteroidOS 2.0 ships with always-on displays, expanded smartwatch hardware support, UI performance gains, and better battery life-a solid update to the Linux-based watch OS that actually works on real devices.

215 points
```

---

## Features

- **Manual mode**: Send `/summarize <hn_url>` to the bot or just paste a HN link
- **Auto mode**: Polls HN top stories on a schedule, posts new ones to a channel
- **Smart summaries**: Uses your configured LLM provider (Claude or OpenAI-compatible) for a concise 1-2 sentence summary (usually one sentence)
- **Deduplication**: SQLite-backed store prevents reposts
- **Graceful fallbacks**: Works even if article scraping fails (summarizes from title)

---

## Setup

### 1. Get your credentials

**Telegram Bot Token:**
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the token it gives you

**LLM API Key:**
1. If using Claude: create a key at [console.anthropic.com](https://console.anthropic.com)
2. If using OpenAI: create a key at [platform.openai.com](https://platform.openai.com)

### 2. Install

```bash
# Clone the repo
git clone https://github.com/yourname/hntldr
cd hntldr

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:
```
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHI...
LLM_PROVIDER=claude
LLM_MODEL=claude-haiku-4-5-20251001
LLM_API_KEY=your_key_here
ADMIN_USER_ID=123456789
TELEGRAM_CHANNEL_ID=@yourchannel  # optional, for auto-posting
```

### 4. Run

```bash
python src/bot.py
```

---

## Bot Commands

| Command | Description |
|---|---|
| `/summarize <hn_url_or_id>` | Summarize a specific HN story (admin only) |
| `/start` | Show intro + usage |
| `/help` | Show usage info |

You can also just paste a HN URL directly in chat — the bot will auto-detect and summarize it.

---

## Auto-Posting to a Channel

Set `TELEGRAM_CHANNEL_ID` in your `.env`:

```
TELEGRAM_CHANNEL_ID=@mychannelname
# or for private channels:
TELEGRAM_CHANNEL_ID=-1001234567890
```

Then add your bot as an **admin** of the channel (it needs "Post Messages" permission).

The bot polls HN every `POLL_INTERVAL_MINUTES` minutes and posts up to `STORIES_PER_POLL` stories that pass per-topic score thresholds (`MIN_SCORE_*`).

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *required* | Bot token from BotFather |
| `LLM_PROVIDER` | `claude` | `claude` or `openai` |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | Model name for selected provider |
| `LLM_API_KEY` | *required* | API key for selected provider |
| `LLM_MAX_TOKENS` | `300` | Max response tokens for summaries |
| `ADMIN_USER_ID` | *(empty)* | Comma-separated Telegram user IDs for `/summarize` + error DMs |
| `TELEGRAM_CHANNEL_ID` | *(empty)* | Channel to auto-post to |
| `POLL_INTERVAL_MINUTES` | `60` | How often to check HN |
| `MIN_SCORE_DEFAULT` | `100` | Default min score for auto-post |
| `MIN_SCORE_SHOW_HN` | `50` | Min score for Show HN |
| `MIN_SCORE_ASK_HN` | `100` | Min score for Ask HN |
| `MIN_SCORE_LAUNCH_HN` | `75` | Min score for Launch HN |
| `MIN_SCORE_TELL_HN` | `100` | Min score for Tell HN |
| `MIN_SCORE_JOBS` | `-1` | Min score for jobs (`-1` disables job posts) |
| `STORIES_PER_POLL` | `3` | Max posts per cycle |
| `MAX_ARTICLE_CHARS` | `4000` | Article chars sent to Claude |
| `REQUEST_TIMEOUT` | `15` | HTTP timeout (seconds) |
| `DB_PATH` | `hntldr.db` | SQLite database path |
| `OPENAI_BASE_URL` | *(unset)* | Optional OpenAI-compatible base URL override |

---

## Cost Estimate

Assumptions for rough daily cost:

- 72 summaries/day (3 stories/hour x 24 hours)
- ~500 input tokens + ~120 output tokens per summary
- Daily token volume: 36,000 input + 8,640 output

Estimated API cost (standard token pricing):

| Model | Input $/MTok | Output $/MTok | Est. $/day | Est. $/30-day month |
|---|---:|---:|---:|---:|
| Claude Sonnet 4.5 | 3.00 | 15.00 | 0.238 | 7.13 |
| Claude Sonnet 4.6 | 3.00 | 15.00 | 0.238 | 7.13 |
| Claude Haiku 4.5 | 1.00 | 5.00 | 0.079 | 2.38 |
| OpenAI GPT-5.2 (example) | 1.75 | 14.00 | 0.184 | 5.52 |
| OpenAI GPT-5 mini (example) | 0.25 | 2.00 | 0.026 | 0.79 |

Pricing changes over time, so treat these as planning estimates and verify current rates before budgeting:

- Anthropic pricing: https://www.anthropic.com/pricing
- OpenAI pricing: https://openai.com/api/pricing

---

## Deployment

### Railway (recommended)
```bash
# Install Railway CLI, then:
railway login
railway init
railway up
```
Set env vars in the Railway dashboard.

### Systemd (VPS)
```ini
[Unit]
Description=hntldr Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/hntldr
ExecStart=/opt/hntldr/venv/bin/python src/bot.py
Restart=always
RestartSec=10
EnvironmentFile=/opt/hntldr/.env

[Install]
WantedBy=multi-user.target
```

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "src/bot.py"]
```

---

## Project Structure

```
hntldr/
├── src/
│   ├── bot.py          # Entry point, Telegram handlers
│   ├── config.py       # Configuration from env vars
│   ├── fetcher.py      # HN API + article scraping
│   ├── summarizer.py   # Claude summarization
│   ├── formatter.py    # Telegram message formatting
│   ├── llm.py          # LLM provider abstraction
│   ├── scheduler.py    # Auto-polling scheduler
│   ├── store.py        # SQLite deduplication store
│   ├── updater.py      # Live score/comment updater
│   └── errors.py       # Admin error notifications
├── requirements.txt
└── .env.example
```
