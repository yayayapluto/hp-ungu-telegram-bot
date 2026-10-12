# hp-ungu — Telegram Remote Administration for Android (Termux + Ubuntu)

Personal, single-owner remote administration system for an Android phone.

* **Ubuntu (proot-distro)** runs the Telegram bot, command execution, job
  management, file manager, monitoring and tests.
* **Native Termux** runs a minimal, token-authenticated HTTP bridge that wraps
  an explicit allow-list of Termux:API commands, plus PM2/SSH diagnostics.
* **Native Termux PM2** supervises both the bridge and the Ubuntu bot.

Default deployment target: `CHANGE_ME.tailXXXXXX.ts.net`, SSH port `8022`,
user `CHANGE_ME_TERMUX_USER`.

---

## 1. Layout

```
hp-ungu-telegram-bot/            # Git working tree, lives in Termux $HOME
├── .env                         # created by install.sh (NEVER committed)
├── .env.example
├── requirements.txt
├── ubuntu/                      # runs INSIDE Ubuntu (venv inside Ubuntu)
├── termux/                      # runs in NATIVE Termux
└── tests/
```

Source files are shared from Termux storage; **the Python interpreter,
dependencies and execution environment are inside Ubuntu**. `install.sh`
detects the Ubuntu-side path and records it in `termux/paths.env`.

---

## 2. Prerequisites on the phone

* Termux (F-Droid or GitHub build — **not** the Play Store build)
* Termux:API app + `termux-api` package
* Termux:Boot app
* `proot-distro` with an Ubuntu rootfs installed
* OpenSSH (already running on port 8022)
* Tailscale (app and/or CLI)
* Node.js + PM2 in native Termux

## 3. Installation

```bash
# --- in native Termux ---
pkg update
pkg install -y git

git clone https://github.com/yayayapluto/hp-ungu-telegram-bot.git ~/hp-ungu-telegram-bot
cd ~/hp-ungu-telegram-bot
chmod +x termux/*.sh termux/boot/*.sh
bash termux/install.sh
```

`install.sh` will:

1. Verify it is running inside Termux and that `$PREFIX` looks correct.
2. Install/verify `python`, `termux-api`, `proot-distro`, `openssh`, `git`,
   `nodejs`, `pm2`.
3. Detect the installed Ubuntu distribution.
4. Create `.env` from `.env.example` and generate a strong
   `TERMUX_BRIDGE_TOKEN`.
5. Prompt for `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ADMIN_IDS` if placeholders.
6. Enter Ubuntu once to install `python3`, `python3-venv`, `python3-pip`,
   create `.venv` and `pip install -r requirements.txt`.
7. Detect the Ubuntu-side project path and write `termux/paths.env`.
8. `pm2 start` the bridge and the Ubuntu bot, then `pm2 save`.
9. Install the Termux:Boot script.
10. Run a full health check.

Then:

```bash
termux-wake-lock            # optional but recommended
bash termux/healthcheck.sh
```

### First-run requirements

* Open the **Termux:Boot** app once after install so Android registers the
  boot receiver.
* Open the **Termux:API** app once and grant the permissions you intend to use
  (camera, microphone, location, storage, notifications).
* Disable battery optimisation for Termux / Termux:Boot if your OEM allows it.
  **PM2 + Termux:Boot do not guarantee uninterrupted execution** — Android may
  still kill background processes.

---

## 4. Configuration (`.env`)

| Key | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_ADMIN_IDS` | Comma-separated numeric Telegram user IDs |
| `DEFAULT_WORKING_DIRECTORY` | Initial cwd inside Ubuntu |
| `COMMAND_TIMEOUT_SECONDS` | Per-command timeout |
| `MAX_CONCURRENT_JOBS` | Job concurrency limit |
| `MAX_OUTPUT_BYTES` | In-memory capture limit before truncation |
| `ENABLE_UNRESTRICTED_SHELL` | Master switch for `/sh` |
| `ALLOWED_FILE_ROOTS` | `:`-separated absolute roots for the file manager |
| `TERMUX_BRIDGE_HOST` / `_PORT` / `_TOKEN` | Bridge address + shared secret |
| `SSH_HOSTNAME`, `SSH_PORT`, `SSH_USERNAME`, `TAILSCALE_IP` | `/ssh` output |
| `LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

Never commit `.env`.

---

## 5. Telegram usage

### Slash commands

```
/start /help /health /status
/cmd <argv...>        safe argv execution inside Ubuntu
/sh  <shell string>   shell mode (pipes, redirection) — gated
/cwd /cd <path>
/jobs /cancel <job-id|all>
/processes /logs [n]
/battery /device /network /storage
/termuxapi /termux <op> [json]
/pm2 /pm2logs <name> [n]
/ssh /sshstatus
/files /settings
```

### Menu

`/start` renders an inline keyboard with: Terminal, Ubuntu Status, Device
Status, Battery, Storage, Network, Processes, PM2, Termux:API, File Manager,
Logs, Running Jobs, SSH Connection, Settings.

All handlers are admin-filtered. Non-admins get a single polite refusal.

---

## 6. SSH from Windows

```
ssh -p 8022 CHANGE_ME_TERMUX_USER@CHANGE_ME.tailXXXXXX.ts.net
ssh -p 8022 CHANGE_ME_TERMUX_USER@100.64.0.0
```

Port **8022**, not 22. `/ssh` prints these plus live diagnostics; `/sshstatus`
checks the sshd process, the PM2 entry, the configured port, a real TCP
connect to `127.0.0.1:8022`, and Tailscale reachability.

---

## 7. Update / rollback

```bash
bash termux/update.sh          # git pull + pip install + pm2 restart
```

Rollback:

```bash
cd ~/hp-ungu-telegram-bot
git log --oneline -n 5
git checkout <good-sha>
bash termux/update.sh --no-pull
```

Full recovery after a failed deployment:

```bash
bash termux/install.sh --repair
```

---

## 8. Tests

```bash
# inside Ubuntu
cd <ubuntu project dir>
.venv/bin/python -m pytest -q
```

Automated (desktop-safe): security/path rules, command runner, timeouts,
cancellation, output truncation, job manager, file manager, bridge auth and
validation, formatting.

Must be performed on the physical device: real Termux:API calls (camera,
microphone, location, torch, clipboard), PM2 control, Termux:Boot reboot
recovery, Tailscale reachability.

No test is claimed to have passed here — they were not executed in this
environment.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Bridge health fails | bridge not running | `pm2 logs hp-ungu-bridge`; `bash termux/healthcheck.sh` |
| Ubuntu can't reach `127.0.0.1:8765` | unusual PRoot networking | run `bash termux/healthcheck.sh` — it prints the exact failing layer and suggests the socket fallback |
| `Unavailable` for battery | Termux:API app missing/permission denied | open Termux:API app, grant permission |
| `/sh` refuses | master switch off | set `ENABLE_UNRESTRICTED_SHELL=true` in `.env` **and** enable in `/settings` |
| SSH refused | sshd not running | `pm2 restart sshd` (never done automatically by the bot) |
| DNS failure to `*.ts.net` | Tailscale down on Windows | `tailscale status` on both ends; use the IP form |
| Bot dead after reboot | Termux:Boot not opened once | open the app, re-run `install.sh` |
