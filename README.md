<h1 align="center">🏠 Property Manager Bot</h1>

<p align="center">
  <strong>A Discord bot for managing rental properties, tracking payments, and sending automated reminders.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/discord.py-2.3%2B-5865F2?logo=discord&logoColor=white" alt="discord.py 2.3+">
  <img src="https://img.shields.io/badge/database-SQLite-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

---

## ✨ Features

- 🔗 **CID Linking** — Link Discord accounts to in-game Character IDs
- 📊 **Rent Data Import** — Upload `.csv` / `.txt` files, paste CSV, or reference Discord messages
- 🔔 **Automated Reminders** — Daily rent reminders posted to a channel **and** delivered via DMs
- 📈 **Admin Reports** — Financial summaries, status breakdowns, and top debtor tracking
- 🌐 **Multi-Server Support** — Fully isolated per-guild data with unique CID ownership
- 📖 **Interactive Help** — Dropdown-based `/help` command with categorized navigation

## 📋 Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/help` | Interactive help menu with categories | Everyone |
| `/link-cid` | Link your Discord to an in-game CID | Everyone |
| `/unlink-cid` | Remove a CID link | Everyone |
| `/my-cids` | View your linked CIDs | Everyone |
| `/update-data` | Upload rent data (file, CSV, or message link) | Admin |
| `/rent-summary` | View rent collection summary | Admin |
| `/all-links` | View all Discord ↔ CID links | Admin |
| `/set-rent-channel` | Set channel for rent reminders | Admin |
| `/set-deadline` | Set the rent payment deadline date | Admin |
| `/send-reminders` | Manually trigger rent reminders | Admin |

## 🚀 Setup

### Prerequisites

- **Python 3.10+**
- A [Discord Bot](https://discord.com/developers/applications) with the following enabled:
  - `MESSAGE CONTENT` intent
  - `SERVER MEMBERS` intent
  - Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Use Application Commands`

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/BlackBox-cmd/Property_Manager.git
   cd Property_Manager
   ```

2. **Create a virtual environment** (recommended)
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and replace `your_token_here` with your actual bot token.

5. **Run the bot**
   ```bash
   python bot.py
   ```

## 📁 Project Structure

```
Property_Manager/
├── bot.py              # Entry point — loads cogs, starts the bot
├── config.py           # Shared constants (footer text, activities)
├── database.py         # SQLite database layer (CID links, settings, rent data)
├── requirements.txt    # Python dependencies
├── .env.example        # Template for environment variables
├── .gitignore          # Git ignore rules
└── cogs/
    ├── __init__.py
    ├── admin.py        # /rent-summary, /all-links
    ├── data.py         # /update-data (CSV/file/message link ingestion)
    ├── help.py         # /help (interactive dropdown)
    ├── linking.py      # /link-cid, /unlink-cid, /my-cids
    └── reminders.py    # /set-rent-channel, /send-reminders, daily task + DMs
```

## 🔔 Reminder System

The bot supports **dual delivery** for rent reminders:

1. **Channel Post** — Reminders are posted in the configured rent channel with user mentions
2. **Direct Messages** — Each linked user also receives a personal DM with their outstanding rent

> DMs fail gracefully — if a user has DMs disabled, the bot logs it and continues. Channel reminders are always sent regardless.

Reminders run **every 24 hours** automatically and can also be triggered manually by admins.

## ⚙️ Configuration

| Setting | How to Set | Description |
|---------|-----------|-------------|
| `DISCORD_TOKEN` | `.env` file | Your bot's authentication token |
| Rent Channel | `/set-rent-channel` | Where reminders get posted |
| Rent Deadline | `/set-deadline` | Date by which rent must be paid (defaults to 18th of current month) |

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<p align="center">Made with ❤️ by <strong>Mr_Freak_cmd</strong></p>
