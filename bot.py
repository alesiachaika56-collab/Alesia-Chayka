#!/usr/bin/env python3
"""Heavenly Stories Telegram bot.

Uses only Python's standard library and Telegram Bot API long polling.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
SUBSCRIPTION_STARS = int(os.environ.get("SUBSCRIPTION_STARS", "250"))
SUPPORT_USERNAME = os.environ.get("SUPPORT_USERNAME", "@HeavenlyStoriesBot").strip()
DATABASE_PATH = os.environ.get("DATABASE_PATH", "/data/heavenly_stories.db")
API = f"https://api.telegram.org/bot{TOKEN}"
MONTH_SECONDS = 2_592_000
SUBSCRIPTION_PAYLOAD = "heavenly_stories_monthly_v1"

WELCOME = (
    "Добро пожаловать в «Небесные истории» ✨\n\n"
    "Здесь живут добрые христианские аудиосказки, которые помогают детям "
    "узнавать Божью любовь, веру и надежду.\n\n"
    "Выберите, что хотите послушать:"
)

TERMS = (
    "Условия использования\n\n"
    "Подписка открывает доступ к платной библиотеке на 30 дней и автоматически "
    "продлевается через Telegram Stars до отмены. Цифровые материалы предназначены "
    "для личного прослушивания. По вопросам оплаты и возврата обратитесь через "
    "/paysupport. Оплачивая подписку, вы соглашаетесь с этими условиями."
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("heavenly-stories")


def db() -> sqlite3.Connection:
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(db()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('audio', 'voice')),
                is_free INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                expires_at INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                charge_id TEXT,
                updated_at INTEGER NOT NULL
            );
            """
        )
        conn.commit()


def api(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{API}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=65) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API {method} failed: {exc.code} {body}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed: {result}")
    return result


def send_message(chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    api("sendMessage", payload)


def main_keyboard() -> list[list[dict[str, str]]]:
    return [
        [{"text": "🎧 Бесплатная сказка", "callback_data": "free"}],
        [{"text": "✨ Библиотека сказок", "callback_data": "library"}],
        [{"text": "⭐ Оформить подписку", "callback_data": "subscribe"}],
        [{"text": "👤 Моя подписка", "callback_data": "status"}],
    ]


def is_subscribed(user_id: int) -> bool:
    now = int(time.time())
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT expires_at, state FROM subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["state"] == "active" and row["expires_at"] > now)


def send_story(chat_id: int, row: sqlite3.Row) -> None:
    method = "sendAudio" if row["media_type"] == "audio" else "sendVoice"
    api(method, {"chat_id": chat_id, row["media_type"]: row["file_id"], "caption": row["title"]})


def show_free(chat_id: int) -> None:
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT * FROM stories WHERE is_free = 1 ORDER BY id LIMIT 1"
        ).fetchone()
    if row:
        send_story(chat_id, row)
    else:
        send_message(chat_id, "Первая бесплатная сказка скоро появится здесь 🌿")


def show_library(chat_id: int, user_id: int) -> None:
    if not is_subscribed(user_id) and user_id != ADMIN_USER_ID:
        send_message(
            chat_id,
            "Эта библиотека открывается после оформления подписки ✨",
            [[{"text": "⭐ Оформить подписку", "callback_data": "subscribe"}]],
        )
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM stories ORDER BY id").fetchall()
    if not rows:
        send_message(chat_id, "Библиотека скоро наполнится новыми историями 🌿")
        return
    send_message(chat_id, f"В библиотеке {len(rows)} историй. Приятного прослушивания ✨")
    for row in rows:
        send_story(chat_id, row)


def send_invoice(chat_id: int) -> None:
    api(
        "sendInvoice",
        {
            "chat_id": chat_id,
            "title": "Небесные истории — подписка",
            "description": "Доступ ко всей библиотеке аудиосказок на 30 дней",
            "payload": SUBSCRIPTION_PAYLOAD,
            "provider_token": "",
            "currency": "XTR",
            "prices": [{"label": "Подписка на 30 дней", "amount": SUBSCRIPTION_STARS}],
            "subscription_period": MONTH_SECONDS,
        },
    )


def status_text(user_id: int) -> str:
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT expires_at, state FROM subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row or row["expires_at"] <= int(time.time()) or row["state"] != "active":
        return "Сейчас у вас нет активной подписки."
    date = time.strftime("%d.%m.%Y", time.localtime(row["expires_at"]))
    return f"Ваша подписка активна до {date} ✨"


def add_story(message: dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    if user_id != ADMIN_USER_ID:
        send_message(chat_id, "Эта команда доступна только автору бота.")
        return
    reply = message.get("reply_to_message") or {}
    media = reply.get("audio") or reply.get("voice")
    media_type = "audio" if reply.get("audio") else "voice"
    parts = (message.get("text") or "").split(maxsplit=2)
    if not media or len(parts) < 3 or parts[1] not in {"free", "premium"}:
        send_message(
            chat_id,
            "Ответьте этой командой на аудиофайл:\n/addstory free Название\n"
            "или\n/addstory premium Название",
        )
        return
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO stories(title, file_id, media_type, is_free, created_at) VALUES(?,?,?,?,?)",
            (parts[2], media["file_id"], media_type, int(parts[1] == "free"), int(time.time())),
        )
        conn.commit()
    send_message(chat_id, f"Готово: «{parts[2]}» добавлена в библиотеку.")


def handle_message(message: dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)
    text = (message.get("text") or "").split("@", 1)[0]
    if text.startswith("/addstory"):
        add_story(message)
    elif text in {"/start", "/menu"}:
        send_message(chat_id, WELCOME, main_keyboard())
    elif text == "/terms":
        send_message(chat_id, TERMS)
    elif text in {"/support", "/paysupport"}:
        send_message(
            chat_id,
            f"По вопросам работы бота и оплаты напишите: {SUPPORT_USERNAME}\n"
            "Telegram не обрабатывает обращения по покупкам в этом боте.",
        )
    elif text == "/status":
        send_message(chat_id, status_text(user_id))
    elif message.get("successful_payment"):
        payment = message["successful_payment"]
        if payment.get("invoice_payload") != SUBSCRIPTION_PAYLOAD:
            return
        expires = payment.get("subscription_expiration_date") or int(time.time()) + MONTH_SECONDS
        with closing(db()) as conn:
            conn.execute(
                "INSERT INTO subscriptions(user_id, expires_at, state, charge_id, updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "expires_at=excluded.expires_at, state='active', charge_id=excluded.charge_id, "
                "updated_at=excluded.updated_at",
                (user_id, expires, "active", payment.get("telegram_payment_charge_id"), int(time.time())),
            )
            conn.commit()
        send_message(chat_id, "Оплата прошла успешно! Библиотека открыта ✨", main_keyboard())


def handle_callback(query: dict[str, Any]) -> None:
    api("answerCallbackQuery", {"callback_query_id": query["id"]})
    chat_id = query["message"]["chat"]["id"]
    user_id = query["from"]["id"]
    action = query.get("data")
    if action == "free":
        show_free(chat_id)
    elif action == "library":
        show_library(chat_id, user_id)
    elif action == "subscribe":
        send_message(chat_id, TERMS)
        send_invoice(chat_id)
    elif action == "status":
        send_message(chat_id, status_text(user_id))


def handle_update(update: dict[str, Any]) -> None:
    if "pre_checkout_query" in update:
        query = update["pre_checkout_query"]
        ok = query.get("invoice_payload") == SUBSCRIPTION_PAYLOAD
        payload: dict[str, Any] = {"pre_checkout_query_id": query["id"], "ok": ok}
        if not ok:
            payload["error_message"] = "Не удалось проверить эту подписку."
        api("answerPreCheckoutQuery", payload)
    elif "callback_query" in update:
        handle_callback(update["callback_query"])
    elif "subscription" in update:
        subscription = update["subscription"]
        user_id = subscription["user"]["id"]
        with closing(db()) as conn:
            conn.execute(
                "UPDATE subscriptions SET state = ?, updated_at = ? WHERE user_id = ?",
                (subscription["state"], int(time.time()), user_id),
            )
            conn.commit()
    elif "message" in update:
        handle_message(update["message"])


def set_commands() -> None:
    api(
        "setMyCommands",
        {
            "commands": [
                {"command": "start", "description": "Открыть главное меню"},
                {"command": "status", "description": "Проверить подписку"},
                {"command": "terms", "description": "Условия использования"},
                {"command": "support", "description": "Помощь"},
                {"command": "paysupport", "description": "Помощь с оплатой"},
            ]
        },
    )


def run() -> None:
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    if ADMIN_USER_ID <= 0:
        raise SystemExit("ADMIN_USER_ID is required")
    init_db()
    set_commands()
    offset = 0
    log.info("Heavenly Stories bot started")
    while True:
        try:
            result = api(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 50,
                    "allowed_updates": ["message", "callback_query", "pre_checkout_query", "subscription"],
                },
            )
            for update in result["result"]:
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception:
                    log.exception("Failed to handle update %s", update.get("update_id"))
        except Exception:
            log.exception("Polling failed; retrying")
            time.sleep(5)


if __name__ == "__main__":
    run()
