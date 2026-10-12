from __future__ import annotations

from ubuntu.utils.formatting import (
    escape_html,
    human_bytes,
    human_duration,
    pre,
    progress_bar,
    shorten,
    truncate,
)


def test_human_bytes():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1023) == "1023 B"
    assert human_bytes(1024) == "1.0 KiB"
    assert human_bytes(1024 * 1024) == "1.0 MiB"
    assert human_bytes(None) == "Unavailable"


def test_human_duration():
    assert human_duration(0) == "0s"
    assert human_duration(65) == "1m 5s"
    assert human_duration(3661) == "1h 1m 1s"
    assert human_duration(90061) == "1d 1h 1m"


def test_escape_html_blocks_injection():
    assert escape_html("<b>") == "&lt;b&gt;"


def test_pre_escapes_content():
    rendered = pre("<script>alert(1)</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_truncate():
    assert truncate("abcdef", 10) == "abcdef"
    assert "truncated" in truncate("x" * 100, 20)


def test_progress_bar_bounds():
    assert "100.0%" in progress_bar(200)
    assert "0.0%" in progress_bar(-5)


def test_shorten_strips_newlines():
    assert "\n" not in shorten("a\nb\nc", 100)
```

---

## Deployment walkthrough

```bash
# On your workstation
git init hp-ungu-telegram-bot && cd hp-ungu-telegram-bot
# ... create every file from this document ...
git add . && git commit -m "Initial hp-ungu implementation"
git remote add origin git@github.com:<you>/hp-ungu-telegram-bot.git
git push -u origin main
```

```bash
# On the phone, in native Termux
pkg install -y git
git clone https://github.com/<you>/hp-ungu-telegram-bot.git ~/hp-ungu-telegram-bot
cd ~/hp-ungu-telegram-bot
chmod +x termux/*.sh termux/boot/*.sh
bash termux/install.sh
```

Then, from Windows:

```powershell
ssh -p 8022 CHANGE_ME_TERMUX_USER@CHANGE_ME.tailXXXXXX.ts.net
