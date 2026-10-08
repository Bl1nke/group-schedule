import unittest
from datetime import datetime
from bot import day_off_text, lessons_for_day, reminder_text


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


if __name__ == "__main__":
    unittest.main()
