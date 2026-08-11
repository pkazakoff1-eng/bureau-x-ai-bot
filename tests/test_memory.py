import importlib
import os
import sys
import tempfile
import types
import unittest


class GlobalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.update({
            "TELEGRAM_TOKEN": "test",
            "ANTHROPIC_KEY": "test",
            "TAVILY_KEY": "test",
            "WAVESPEED_KEY": "test",
            "DB_PATH": os.path.join(self.tmp.name, "memory.db"),
        })
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda: None
        sys.modules.setdefault("dotenv", dotenv)
        for name in ("src.db", "src.config"):
            sys.modules.pop(name, None)
        self.db = importlib.import_module("src.db")
        self.db.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_topic_does_not_filter_conversation_history(self):
        user_id = 999
        self.db.save_message(user_id, "user", "Рабочий контекст", "работа")
        self.db.save_message(user_id, "assistant", "Принял", "работа")
        self.db.save_message(user_id, "user", "Продолжим дома", "личное")

        history = self.db.get_history(user_id, topic="личное")

        self.assertEqual(
            [item["content"] for item in history],
            ["Рабочий контекст", "Принял", "Продолжим дома"],
        )

    def test_topic_history_is_available_only_for_note_maintenance(self):
        user_id = 1000
        self.db.save_message(user_id, "user", "Работа", "работа")
        self.db.save_message(user_id, "assistant", "Ответ", "работа")
        self.db.save_message(user_id, "user", "Дом", "личное")

        notes_history = self.db.get_topic_history(user_id, "работа")

        self.assertEqual(
            [item["content"] for item in notes_history],
            ["Работа", "Ответ"],
        )


if __name__ == "__main__":
    unittest.main()
