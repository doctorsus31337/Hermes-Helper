from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


TOKEN_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "BOT_TOKEN")


@dataclass(frozen=True)
class TelegramHealth:
    configured: bool
    reachable: bool
    bot_name: str = ""
    detail: str = ""


def read_env_value(paths: list[Path], keys: tuple[str, ...]) -> str:
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in keys:
                return value.strip().strip("'\"")
    return ""


def token_for_home(hermes_home: Path) -> str:
    return read_env_value(
        [hermes_home / ".env", hermes_home / "hermes-agent" / ".env"],
        TOKEN_KEYS,
    )


def _api(token: str, method: str, payload: dict[str, str] | None = None, timeout: float = 5.0) -> dict:
    data = urllib.parse.urlencode(payload).encode() if payload else None
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check_health(hermes_home: Path) -> TelegramHealth:
    token = token_for_home(hermes_home)
    if not token:
        return TelegramHealth(False, False, detail="No Telegram bot token was found in the Hermes environment.")
    try:
        data = _api(token, "getMe")
        result = data.get("result", {})
        name = result.get("username") or result.get("first_name") or "Telegram bot"
        return TelegramHealth(True, bool(data.get("ok")), str(name), "Telegram Bot API responded successfully.")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return TelegramHealth(True, False, detail=f"Telegram API check failed: {type(exc).__name__}")


def parse_chat_ids(log_text: str) -> list[str]:
    patterns = (
        r'["\']?chat_id["\']?\s*[:=]\s*["\']?(-?\d+)',
        r'chat(?:\s+|_)(?:id\s+)?[=:]\s*(-?\d+)',
    )
    found: set[str] = set()
    for pattern in patterns:
        found.update(re.findall(pattern, log_text, flags=re.IGNORECASE))
    return sorted(found, key=lambda item: int(item))


def send_message(hermes_home: Path, chat_id: str, text: str) -> str:
    token = token_for_home(hermes_home)
    if not token:
        raise RuntimeError("Telegram is not configured: no bot token was found.")
    if not re.fullmatch(r"-?\d+", chat_id.strip()):
        raise ValueError("Chat ID must contain only digits (with an optional leading minus sign).")
    data = _api(token, "sendMessage", {"chat_id": chat_id.strip(), "text": text}, timeout=12)
    if not data.get("ok"):
        raise RuntimeError("Telegram rejected the message.")
    return f"Message sent to Telegram chat {chat_id.strip()}."

