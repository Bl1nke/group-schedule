"""Telegram-напоминания о занятиях группы ВИС23."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

SCHEDULE_URL = "https://lk.donstu.ru/api/Rasp"
STATE_PATH = Path("data/bot_state.json")
FETCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class Settings:
    token: str
    chat_id: int
    group_id: int = 73001
    timezone: ZoneInfo = ZoneInfo("Europe/Moscow")
    reminder_minutes: int = 20
    daily_schedule_hour: int = 7
    daily_schedule_minute: int = 30
    send_day_off_message: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        chat_id = os.getenv("CHAT_ID", "").strip()
        if not token or not chat_id:
            raise RuntimeError("Заполните BOT_TOKEN и CHAT_ID в файле .env")
        return cls(
            token=token,
            chat_id=int(chat_id),
            group_id=int(os.getenv("GROUP_ID", "73001")),
            timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow")),
            reminder_minutes=int(os.getenv("REMINDER_MINUTES", "20")),
            daily_schedule_hour=int(os.getenv("DAILY_SCHEDULE_HOUR", "7")),
            daily_schedule_minute=int(os.getenv("DAILY_SCHEDULE_MINUTE", "30")),
            send_day_off_message=os.getenv("SEND_DAY_OFF_MESSAGE", "true").lower()
            not in {"0", "false", "no", "off"},
        )


class SentState:
    """Небольшое постоянное хранилище, предотвращающее повторы после рестарта."""

    def __init__(self, timezone: ZoneInfo, path: Path = STATE_PATH) -> None:
        self.path = path
        self.timezone = timezone
        self.keys = self._load()

    def _load(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def contains(self, key: str) -> bool:
        return key in self.keys

    def add(self, key: str) -> None:
        self.keys.add(key)
        # Оставляем только последние 60 дней, чтобы файл не рос бесконечно.
        cutoff = (datetime.now(self.timezone).date() - timedelta(days=60)).isoformat()
        self.keys = {item for item in self.keys if item >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self.keys), ensure_ascii=False), encoding="utf-8")


async def fetch_lessons(session: aiohttp.ClientSession, settings: Settings) -> list[dict[str, Any]]:
    """Получает расписание с публичного API ДГТУ."""
    async with session.get(
        SCHEDULE_URL,
        params={"idGroup": settings.group_id},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)
    if payload.get("state") != 1:
        raise RuntimeError(f"ДГТУ вернуло ошибку: {payload.get('msg', 'неизвестная ошибка')}")
    return (payload.get("data") or {}).get("rasp", [])


def lessons_for_day(lessons: list[dict[str, Any]], day: datetime) -> list[dict[str, Any]]:
    date_prefix = day.date().isoformat()
    result = [item for item in lessons if str(item.get("датаНачала", "")).startswith(date_prefix)]
    return sorted(result, key=lambda item: str(item.get("датаНачала", "")))


def lesson_start(lesson: dict[str, Any], tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(lesson["датаНачала"]).replace(tzinfo=tz)


def format_lesson(lesson: dict[str, Any]) -> dict[str, str]:
    """Подготавливает поля пары для безопасной HTML-разметки Telegram."""
    return {
        "subject": html.escape(str(lesson.get("дисциплина") or "Предмет не указан")),
        "teacher": html.escape(str(lesson.get("преподаватель") or "Преподаватель не указан")),
        "room": html.escape(str(lesson.get("аудитория") or "уточняется")),
        "start": html.escape(str(lesson.get("начало") or "")),
        "end": html.escape(str(lesson.get("конец") or "")),
    }


def reminder_text(lesson: dict[str, Any], minutes: int) -> str:
    fields = format_lesson(lesson)
    return (
        f"🔔 <b>Через {minutes} мин. начинается пара</b>\n\n"
        f"📚 <b>{fields['subject']}</b>\n"
        f"👨‍🏫 {fields['teacher']}\n"
        f"🕒 {fields['start']}–{fields['end']}\n\n"
        f"📍 <b>АУДИТОРИЯ: {fields['room']}</b>"
    )


def day_off_text(now: datetime) -> str:
    return f"☀️ <b>{now:%d.%m}</b> — сегодня пар нет. Отдыхаем!"


def daily_schedule_text(lessons: list[dict[str, Any]], now: datetime) -> str:
    """Формирует утреннее сообщение с расписанием на текущий день."""
    if not lessons:
        return day_off_text(now)

    items = []
    for number, lesson in enumerate(lessons, start=1):
        fields = format_lesson(lesson)
        items.append(
            f"<b>{number}. {fields['start']}–{fields['end']}</b> — {fields['subject']}\n"
            f"👨‍🏫 {fields['teacher']}\n"
            f"📍 {fields['room']}"
        )
    return f"☀️ <b>Расписание на {now:%d.%m}</b>\n\n" + "\n\n".join(items)


async def scheduler(
    bot: Bot,
    settings: Settings,
    state: SentState,
) -> None:
    """Проверяет расписание для утренней рассылки и напоминаний."""
    all_lessons: list[dict[str, Any]] = []
    last_fetch: datetime | None = None
    has_schedule_data = False
    async with aiohttp.ClientSession(headers={"User-Agent": "VIS23-Schedule-Telegram-Bot/1.0"}) as session:
        while True:
            now = datetime.now(settings.timezone)
            try:
                if last_fetch is None or now - last_fetch >= FETCH_INTERVAL:
                    # Обновляем время до запроса, чтобы при сбое не нагружать API каждые 30 секунд.
                    last_fetch = now
                    fetched_lessons = await fetch_lessons(session, settings)

                    all_lessons = fetched_lessons
                    has_schedule_data = True

                if not has_schedule_data:
                    await asyncio.sleep(30)
                    continue

                lessons = lessons_for_day(all_lessons, now)
                daily_schedule_at = now.replace(
                    hour=settings.daily_schedule_hour,
                    minute=settings.daily_schedule_minute,
                )
                daily_schedule_key = f"{now:%Y-%m-%d}:daily_schedule"
                if (
                    daily_schedule_at <= now < daily_schedule_at + timedelta(hours=2)
                    and not state.contains(daily_schedule_key)
                ):
                    if lessons or settings.send_day_off_message:
                        await bot.send_message(settings.chat_id, daily_schedule_text(lessons, now))
                    state.add(daily_schedule_key)

                for lesson in lessons:
                    start = lesson_start(lesson, settings.timezone)
                    reminder_at = start - timedelta(minutes=settings.reminder_minutes)
                    key = f"{start:%Y-%m-%d}:{lesson.get('код', start.isoformat())}"
                    if reminder_at <= now < start and not state.contains(key):
                        await bot.send_message(settings.chat_id, reminder_text(lesson, settings.reminder_minutes))
                        state.add(key)
            except Exception:
                logging.exception("Не удалось проверить расписание")
            await asyncio.sleep(30)


def build_dispatcher(settings: Settings) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer(
            "Я напоминаю ВИС23 о парах на сегодняшний день "
        )

    @dp.message(Command("chatid"))
    async def chat_id(message: Message) -> None:
        await message.answer(f"ID этого чата: <code>{message.chat.id}</code>")

    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode="HTML"))
    task = asyncio.create_task(scheduler(bot, settings, SentState(settings.timezone)))
    task.add_done_callback(log_scheduler_failure)
    try:
        await build_dispatcher(settings).start_polling(bot)
    finally:
        task.cancel()
        await bot.session.close()


def log_scheduler_failure(task: asyncio.Task[None]) -> None:
    """Не даёт незаметно потерять фоновую задачу с напоминаниями."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logging.error(
            "Планировщик завершился с ошибкой",
            exc_info=(type(error), error, error.__traceback__),
        )


if __name__ == "__main__":
    asyncio.run(main())
