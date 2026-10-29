<h1 align="center">🏠 Property Manager Bot</h1>

<p align="center">
  <strong>A Discord bot for managing rental properties, tracking payments, and sending automated reminders.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/discord.py-2.3%2B-5865F2?logo=discord&logoColor=white" alt="discord.py 2.3+">
  <img src="https://img.shields.io/badge/database-PostgreSQL-336791?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

---

## ✨ Features

- 🔗 **CID Linking** — Link Discord accounts to in-game Character IDs
- 📊 **Rent Data Import** — Upload `.csv` / `.txt` files, paste CSV, or reference Discord messages
- 🔔 **Automated Reminders** — Daily rent reminders posted to a channel **and** delivered via DMs
- 📈 **Admin Reports** — Financial summaries, status breakdowns, and top debtor tracking
- 📞 **Phone Directory** — Searchable renter contact list with interactive button pagination and auto-suggestion
- 🎭 **Automatic Role Management** — Auto-assign roles to linked users (Linked User Role + Block Roles based on address)
- 📋 **Log Channel Configuration** — Separate `/logs` commands for role sync logging
- 🌐 **Multi-Server Support** — Fully isolated per-guild data with unique CID ownership
- 🍯 **Honeytrap** — Anti-spam/anti-raid system with 24-hour timeout for detected spam or raiding activity
- 📖 **Interactive Help** — Dropdown-based `/help` command with categorized navigation

## 📋 Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/help` | Interactive help menu with categories | Everyone |
| `/link-cid` | Link your Discord to an in-game CID | Everyone |
| `/unlink-cid` | Remove a CID link | Everyone |
| `/my-cids` | View your linked CIDs | Everyone |
| `/update-data` | Upload rent data (file, CSV, or message link) | Admin |
| `/rent-summary` | View rent collection summary | Admin / Trusted |
| `/all-links` | View all Discord ↔ CID links | Admin / Trusted |
| `/renter-phones` | List/Search renter phone numbers with button pagination and auto-suggestion | Admin / Trusted |
| `/linkedstatus` | Show linked and unlinked CIDs with interactive views | Admin / Trusted |
| `/sys` | Display System Information (Bot Owner only) | Owner |
| `/trust-user` | Add a Discord user to the trusted list | Admin |
| `/untrust-user` | Remove a Discord user from the trusted list | Admin |
| `/role` | Configure automatic role assignments (subcommands below) | Admin |
| `!clearsync` | Wipe out stuck guild-specific slash commands | Admin |
| `/set-rent-channel` | Set channel for rent reminders | Admin |
| `/send-reminders` | Manually trigger rent reminders | Admin |

### Role Management Subcommands

| Subcommand | Description | Access |
|------------|-------------|--------|
| `/role link-role` | Set role assigned when users link their CID | Admin |
| `/role block-role` | Map a block address to a Discord role | Admin |
| `/role logs` | Set channel for role sync operation logs | Admin |
| `/role list` | Show all configured role mappings | Admin |
| `/role resync` | Manually resync all linked users' roles | Admin / Trusted |
| `/role remove-link-role` | Remove the linked user role configuration | Admin |
| `/role remove-block-role` | Remove a block-to-role mapping | Admin |
| `/logs` | Set channel for role sync logs | Admin |
| `/remove-logs` | Remove the configured logs channel | Admin |

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

```text
Property_Manager/
├── bot.py              # Entry point — loads cogs, starts the bot, monitoring integration
├── config.py           # Shared constants (footer text, activities)
├── database.py         # PostgreSQL database layer (CID links, settings, rent data, role mappings)
├── utils.py            # Utility functions (permissions checking)
├── requirements.txt    # Python dependencies
├── .env.example        # Template for environment variables
├── .gitignore          # Git ignore rules
└── cogs/
    ├── __init__.py
    ├── admin.py        # /rent-summary, /all-links, /renter-phones, /linkedstatus, /sys, trust cmds
    ├── data.py         # /update-data (CSV/file/message link ingestion) + role sync
    ├── help.py         # /help (interactive dropdown with categorized navigation)
    ├── linking.py      # /link-cid, /unlink-cid, /my-cids + role assignment on link
    ├── logs.py         # /logs, /remove-logs — configure role operation logging
    ├── roles.py        # /role commands (link-role, block-role, resync, list, remove commands)
    ├── reminders.py    # /set-rent-channel, /send-reminders, daily task + DMs
    └── honeytrap.py    # Anti-spam/anti-raid protection
```

## 🔔 Reminder System

The bot supports **dual delivery** for rent reminders:

1. **Channel Post** — Reminders are posted in the configured rent channel with user mentions
2. **Direct Messages** — Each linked user also receives a personal DM with their outstanding rent

> DMs fail gracefully — if a user has DMs disabled, the bot logs it and continues. Channel reminders are always sent regardless.

Reminders run **every 24 hours** automatically and can also be triggered manually by admins.

## 🎭 Automatic Role Management

The bot can automatically assign Discord roles to users based on their linked CIDs and rental data. This feature includes:

### Linked User Role
When a user successfully links their CID using `/link-cid`, they are automatically assigned a configured "Linked User" role. This role persists even if the user is removed from the rental dataset.

### Block Roles
When rental data is uploaded via `/update-data`, the bot:
1. **Extracts the block name** from each renter's address
   - `"Forum Drive 2 / Apt 1"` → `"Forum Drive"`
   - `"Niland Avenue 1"` → `"Niland Avenue"`
   - `"Clinton Ave / Apt1"` → `"Clinton Ave"`
2. **Assigns the corresponding block role** to linked users automatically
3. **Updates roles** when renters move between blocks
4. **Removes block roles** when renters are no longer in the dataset

### Block Detection
When data is updated, the bot automatically detects blocks that have renters but no roles configured and shows a warning with the list of blocks needing roles.

### Manual Resync
Use `/role resync` to manually resync all linked users' roles at any time (Admin/Trusted users only).

### Setup Workflow
1. **Grant the bot** the `Manage Roles` permission
2. **Configure the Linked User Role**: `/role link-role @LinkedUsers`
3. **Map block names to roles**: `/role block-role "Forum Drive" @ForumDrive`
4. **(Optional) Set logs channel**: `/role logs #role-logs`
5. **Upload data**: `/update-data` triggers automatic role synchronization

### Safety Features
- ✅ Bot validates role hierarchy during configuration (can only assign roles below its own)
- ✅ Never removes unrelated Discord roles — only manages roles configured through this system
- ✅ Graceful error handling for API failures and permission issues
- ✅ Comprehensive logging of all role assignments and removals
- ✅ Synchronous operation ensures data consistency before role changes
- ✅ Detects and reports blocks without configured roles

### Example
```
1. Admin: /role link-role @Linked
2. Admin: /role block-role "Forum Drive" @FD2Role
3. User: /link-cid 292
   → User receives @Linked role
4. Admin: /update-data (uploads data with CID 292 at "Forum Drive 2 / Apt 1")
   → User receives @FD2Role
   → Bot detects "Clinton Ave" has no role configured and warns admin
5. Admin: /update-data (new data shows CID 292 moved to "Clinton Ave / Apt 5")
   → User loses @FD2Role, receives @ClintonRole (if configured)
```

## 🆕 What's New (October 2026)

### Block Extraction Improvements
- **New behavior:** Block names are now extracted by stripping trailing numbers
  - `"Forum Drive 2 / Apt 1"` → `"Forum Drive"` (was: `"Forum Drive 2"`)
  - `"Niland Avenue 1"` → `"Niland Avenue"` (was: `"Niland Avenue 1"`)
  - `"Steele Way 3"` → `"Steele Way"` (was: `"Steele Way 3"`)
- **Benefit:** Configure one role per street, not per apartment number

### New Block Detection
- When data is updated, the bot now **detects blocks without configured roles**
- Shows a warning in `/update-data` response listing all blocks needing roles
- Logs the same information to the configured logs channel
- Example: `⚠️ Blocks Without Roles: Forum Drive, Niland Avenue`

### New Logs Cog
- **Moved:** `/role logs` → `/logs` (single command, no prefix)
- **New:** `/remove-logs` - Remove the logs channel configuration
- **Separate category** in `/help` for easier navigation

### New Role Management Commands
| Command | Description |
|---------|-------------|
| `/role resync` | Manually resync all linked users' roles |
| `/role remove-link-role` | Remove the linked user role configuration |
| `/role remove-block-role <block>` | Remove a block role mapping (with autocomplete) |
| `/remove-logs` | Remove the logs channel configuration |

### Help System Updates
- **New "📋 Logs" category** in `/help` with `/logs` and `/remove-logs`
- **Updated "🎭 Role Management" category** with all role subcommands

## ⚙️ Configuration

| Setting | How to Set | Description |
|---------|-----------|-------------|
| `DISCORD_TOKEN` | `.env` file | Your bot's authentication token |
| `OwnerId` | `.env` file | Discord ID of the bot owner (for `/sys` access) |
| `MONITORING_ENABLED` | `.env` file | Enable/disable monitoring features (default: true) |
| `UPTIME_KUMA_URL` | `.env` file | Optional Uptime Kuma URL for heartbeat monitoring |
| `UPTIME_KUMA_HEARTBEAT` | `.env` file | Heartbeat interval in seconds (default: 30) |
| Rent Channel | `/set-rent-channel` | Where reminders get posted |

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
