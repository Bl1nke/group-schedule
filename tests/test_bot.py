import unittest
from datetime import datetime
from bot import daily_schedule_text, day_off_text, lessons_for_day, reminder_text


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


if __name__ == "__main__":
    unittest.main()
