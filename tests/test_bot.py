import unittest
from datetime import datetime
from bot import (
    daily_schedule_text,
    day_off_text,
    lesson_cancelled_text,
    lessons_by_key,
    lessons_for_day,
    reminder_text,
    schedule_changes,
    schedule_updated_text,
)


class ScheduleFormattingTests(unittest.TestCase):
    def test_filters_only_selected_day(self):
        now = datetime(2026, 10, 8, 9)
        lessons = [{"датаНачала": "2026-10-08T10:15:00"}, {"датаНачала": "2026-10-10T08:30:00"}]
        self.assertEqual(lessons_for_day(lessons, now), [lessons[0]])

    def test_room_is_prominent_and_html_safe(self):
        text = reminder_text({"дисциплина": "лаб <Python>", "преподаватель": "Иванова", "аудитория": "1-305", "начало": "10:15", "конец": "11:50"})
        self.assertIn("АУДИТОРИЯ: 1-305", text)
        self.assertIn("&lt;Python&gt;", text)

    def test_day_off_message(self):
        self.assertIn("пар нет", day_off_text(datetime(2026, 10, 9)))

    def test_daily_schedule_contains_all_lessons(self):
        lessons = [
            {
                "дисциплина": "Программирование <Python>",
                "преподаватель": "Иванова",
                "аудитория": "1-305",
                "начало": "10:15",
                "конец": "11:50",
            },
            {
                "дисциплина": "Математика",
                "преподаватель": "Петров",
                "аудитория": "2-101",
                "начало": "12:00",
                "конец": "13:35",
            },
        ]

        text = daily_schedule_text(lessons, datetime(2026, 10, 9))

        self.assertIn("Расписание на 09.10", text)
        self.assertIn("1. 10:15–11:50", text)
        self.assertIn("2. 12:00–13:35", text)
        self.assertIn("&lt;Python&gt;", text)

    def test_daily_schedule_reports_day_off_when_empty(self):
        self.assertIn("пар нет", daily_schedule_text([], datetime(2026, 10, 9)))

    def test_schedule_changes_include_changed_and_added_lessons(self):
        previous = lessons_by_key([
            {"код": 1, "дисциплина": "Математика", "аудитория": "1-101"},
        ])
        current = lessons_by_key([
            {"код": 1, "дисциплина": "Математика", "аудитория": "2-201"},
            {"код": 2, "дисциплина": "Физика", "аудитория": "3-301"},
        ])

        updated, cancelled = schedule_changes(previous, current)

        self.assertEqual([lesson["код"] for lesson in updated], [1, 2])
        self.assertEqual(cancelled, [])

    def test_schedule_changes_include_cancelled_lessons(self):
        previous = lessons_by_key([
            {"код": 1, "дисциплина": "Математика", "начало": "10:15"},
        ])

        updated, cancelled = schedule_changes(previous, {})

        self.assertEqual(updated, [])
        self.assertEqual(cancelled[0]["код"], 1)
        self.assertIn("отменена", lesson_cancelled_text(cancelled[0]))

    def test_updated_schedule_message_has_current_lesson_information(self):
        text = schedule_updated_text({
            "дисциплина": "лаб <Python>",
            "преподаватель": "Иванова",
            "аудитория": "1-305",
            "начало": "10:15",
            "конец": "11:50",
        })

        self.assertIn("Расписание обновлено", text)
        self.assertIn("&lt;Python&gt;", text)
        self.assertIn("АУДИТОРИЯ: 1-305", text)


if __name__ == "__main__":
    unittest.main()
