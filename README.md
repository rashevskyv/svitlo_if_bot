# 🟡 Svitlo.live Telegram Bot & HA Integration

[ЧИТАТИ УКРАЇНСЬКОЮ](https://github.com/chaichuk/svitlo_live/blob/main/readme.uk.md)

This repository contains a **Telegram Bot** for tracking electricity schedules in Ukraine and a **Home Assistant Integration**.

## 🤖 Telegram Bot (Root)
The primary component of this repository is the Telegram bot that notifies users about power schedule changes.

### Features
- ✅ **Real-time notifications** about schedule updates.
- ✅ **Visual schedules** generated as images.
- ✅ **Multi-region support** (Kyiv, Dnipro, Odesa, Lviv, and more).
- ✅ **Easy setup** via Telegram interface.

### Installation & Running
1. Clone the repository with submodules:
   ```bash
   git clone --recursive https://github.com/rashevskyv/svitlo_if_bot.git
   ```
2. Create a `.env` file based on `.env.example` and add your `BOT_TOKEN`.
3. Run the bot:
   - **Windows:** Double-click `run.bat` or run `.\run.bat` in terminal.
   - **Linux:** Run `./run.sh`.

---

## 🏠 Home Assistant Integration (`external/svitlo_live/`)
The core logic and HA integration are located in the [svitlo_live](https://github.com/chaichuk/svitlo_live) repository, which is included here as a git submodule in the `external/svitlo_live/` directory.

### Features
- ✅ Displays **current power status** (`On / Off`).
- ✅ Detects **next power-on** and **power-off** times.
- ✅ **Smart Caching** and **Precise Ticking**.
- ✅ **HACS Compatible** (requires manual move or symlink).

### Installation via HACS
1. Add this repository as a **Custom Repository** in HACS.
2. Download the integration.
3. **Note:** Since the integration is in a submodule, you may need to manually copy the `external/svitlo_live/svitlo_live` folder to your `custom_components/` directory if HACS doesn't handle the structure automatically.

---

## 🌍 Supported Regions
- Kyiv City & Region
- Dnipro City & Region
- Odesa Region
- Lviv Region
- ... and many others via unified API.

## 💡 Author
Latency audit, regression checks, and diagnostic commands: [2026-10-05 report (Ukrainian)](docs/audit-2026-10-05.md).

- GitHub: [@chaichuk](https://github.com/chaichuk)
- Telegram: [@serhii_chaichuk](https://t.me/serhii_chaichuk)

## 🪪 License
MIT License © 2025
