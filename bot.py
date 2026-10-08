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
SCHEDULE_SNAPSHOT_PATH = Path("data/schedule_snapshot.json")


@dataclass(frozen=True)
class Settings:
    token: str
    chat_id: int
    group_id: int = 73001
    timezone: ZoneInfo = ZoneInfo("Europe/Moscow")
    reminder_minutes: int = 10
    daily_schedule_hour: int = 7
    daily_schedule_minute: int = 30

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
            reminder_minutes=int(os.getenv("REMINDER_MINUTES", "10")),
            daily_schedule_hour=int(os.getenv("DAILY_SCHEDULE_HOUR", "7")),
            daily_schedule_minute=int(os.getenv("DAILY_SCHEDULE_MINUTE", "30")),
        )


class SentState:
    """Небольшое постоянное хранилище, предотвращающее повторы после рестарта."""

    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
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
        self.keys = {item for item in self.keys if item >= (datetime.now().date() - timedelta(days=60)).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self.keys), ensure_ascii=False), encoding="utf-8")


class ScheduleSnapshot:
    """Последняя версия расписания, нужная для поиска изменений между опросами."""

    def __init__(self, path: Path = SCHEDULE_SNAPSHOT_PATH) -> None:
        self.path = path
        self.days = self._load()

    def _load(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get(self, day: str) -> dict[str, dict[str, Any]] | None:
        return self.days.get(day)

    def save(self, day: str, lessons: dict[str, dict[str, Any]]) -> None:
        self.days[day] = lessons
        cutoff = (datetime.now().date() - timedelta(days=7)).isoformat()
        self.days = {date: value for date, value in self.days.items() if date >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.days, ensure_ascii=False, sort_keys=True), encoding="utf-8")


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
    return payload.get("data", {}).get("rasp", [])


def lessons_for_day(lessons: list[dict[str, Any]], day: datetime) -> list[dict[str, Any]]:
    date_prefix = day.date().isoformat()
    result = [item for item in lessons if str(item.get("датаНачала", "")).startswith(date_prefix)]
    return sorted(result, key=lambda item: str(item.get("датаНачала", "")))


def lesson_start(lesson: dict[str, Any], tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(lesson["датаНачала"]).replace(tzinfo=tz)


def reminder_text(lesson: dict[str, Any]) -> str:
    subject = html.escape(str(lesson.get("дисциплина") or "Предмет не указан"))
    teacher = html.escape(str(lesson.get("преподаватель") or "Преподаватель не указан"))
    room = html.escape(str(lesson.get("аудитория") or "уточняется"))
    start = html.escape(str(lesson.get("начало") or ""))
    end = html.escape(str(lesson.get("конец") or ""))
    return (
        "🔔 <b>Через 20 минут начинается пара</b>\n\n"
        f"📚 <b>{subject}</b>\n"
        f"👨‍🏫 {teacher}\n"
        f"🕒 {start}–{end}\n\n"
        f"📍 <b>АУДИТОРИЯ: {room}</b>"
    )


def schedule_updated_text(lesson: dict[str, Any]) -> str:
    """Сообщение об изменённой или новой паре без ложного отсчёта до начала."""
    subject = html.escape(str(lesson.get("дисциплина") or "Предмет не указан"))
    teacher = html.escape(str(lesson.get("преподаватель") or "Преподаватель не указан"))
    room = html.escape(str(lesson.get("аудитория") or "уточняется"))
    start = html.escape(str(lesson.get("начало") or ""))
    end = html.escape(str(lesson.get("конец") or ""))
    return (
        "ℹ️ <b>Расписание обновлено</b>\n\n"
        f"📚 <b>{subject}</b>\n"
        f"👨‍🏫 {teacher}\n"
        f"🕒 {start}–{end}\n\n"
        f"📍 <b>АУДИТОРИЯ: {room}</b>"
    )


def lesson_key(lesson: dict[str, Any]) -> str:
    """Возвращает устойчивый идентификатор пары из данных API."""
    code = lesson.get("код")
    if code:
        return f"code:{code}"
    return ":".join(
        str(lesson.get(field, ""))
        for field in ("датаНачала", "дисциплина", "преподаватель")
    )


def lessons_by_key(lessons: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {lesson_key(lesson): lesson for lesson in lessons}


def schedule_changes(
    previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Возвращает добавленные/изменённые и отменённые пары."""
    updated = [
        lesson
        for key, lesson in current.items()
        if key not in previous or lesson != previous[key]
    ]
    cancelled = [lesson for key, lesson in previous.items() if key not in current]
    return updated, cancelled


def lesson_cancelled_text(lesson: dict[str, Any]) -> str:
    subject = html.escape(str(lesson.get("дисциплина") or "Предмет не указан"))
    start = html.escape(str(lesson.get("начало") or ""))
    return f"ℹ️ <b>Расписание обновлено</b>\n\nПара «<b>{subject}</b>» в {start} отменена."


def day_off_text(now: datetime) -> str:
    return f"☀️ <b>{now:%d.%m}</b> — сегодня пар нет. Отдыхаем!"


def daily_schedule_text(lessons: list[dict[str, Any]], now: datetime) -> str:
    """Формирует утреннее сообщение с расписанием на текущий день."""
    if not lessons:
        return day_off_text(now)

    items = []
    for number, lesson in enumerate(lessons, start=1):
        subject = html.escape(str(lesson.get("дисциплина") or "Предмет не указан"))
        teacher = html.escape(str(lesson.get("преподаватель") or "Преподаватель не указан"))
        room = html.escape(str(lesson.get("аудитория") or "уточняется"))
        start = html.escape(str(lesson.get("начало") or ""))
        end = html.escape(str(lesson.get("конец") or ""))
        items.append(
            f"<b>{number}. {start}–{end}</b> — {subject}\n"
            f"👨‍🏫 {teacher}\n"
            f"📍 {room}"
        )
    return f"☀️ <b>Расписание на {now:%d.%m}</b>\n\n" + "\n\n".join(items)


async def scheduler(
    bot: Bot,
    settings: Settings,
    state: SentState,
    snapshot: ScheduleSnapshot | None = None,
) -> None:
    """Проверяет актуальное расписание раз в 30 секунд."""
    snapshot = snapshot or ScheduleSnapshot()
    async with aiohttp.ClientSession(headers={"User-Agent": "VIS23-Schedule-Telegram-Bot/1.0"}) as session:
        while True:
            now = datetime.now(settings.timezone).replace(second=0, microsecond=0)
            try:
                lessons = lessons_for_day(await fetch_lessons(session, settings), now)
                daily_schedule_at = now.replace(
                    hour=settings.daily_schedule_hour,
                    minute=settings.daily_schedule_minute,
                )
                daily_schedule_key = f"{now:%Y-%m-%d}:daily_schedule"
                if now == daily_schedule_at and not state.contains(daily_schedule_key):
                    await bot.send_message(settings.chat_id, daily_schedule_text(lessons, now))
                    state.add(daily_schedule_key)

                date_key = now.date().isoformat()
                current_lessons = lessons_by_key(lessons)
                previous_lessons = snapshot.get(date_key)
                if previous_lessons is not None:
                    updated, cancelled = schedule_changes(previous_lessons, current_lessons)
                    for lesson in updated:
                        await bot.send_message(settings.chat_id, schedule_updated_text(lesson))
                    for lesson in cancelled:
                        await bot.send_message(settings.chat_id, lesson_cancelled_text(lesson))
                snapshot.save(date_key, current_lessons)

                for lesson in lessons:
                    start = lesson_start(lesson, settings.timezone)
                    reminder_at = start - timedelta(minutes=settings.reminder_minutes)
                    key = f"{start:%Y-%m-%d}:{lesson.get('код', start.isoformat())}"
                    if now == reminder_at and not state.contains(key):
                        await bot.send_message(settings.chat_id, reminder_text(lesson))
                        state.add(key)
            except Exception:
                logging.exception("Не удалось проверить расписание")
            await asyncio.sleep(30)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer("Я напоминаю ВИС23 о парах за 20 минут. Команда /chatid покажет ID этого чата.")

    @dp.message(Command("chatid"))
    async def chat_id(message: Message) -> None:
        await message.answer(f"ID этого чата: <code>{message.chat.id}</code>")

    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode="HTML"))
    task = asyncio.create_task(scheduler(bot, settings, SentState()))
    try:
        await build_dispatcher().start_polling(bot)
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
