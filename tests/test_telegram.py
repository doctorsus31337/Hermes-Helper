import tempfile
import unittest
from pathlib import Path

from hermes_control.telegram import parse_chat_ids, token_for_home


class TelegramTests(unittest.TestCase):
    def test_chat_ids_are_parsed_and_deduplicated(self):
        text = 'chat_id: 123\n{"chat_id": "-456"}\nchat_id=123\n'
        self.assertEqual(parse_chat_ids(text), ["-456", "123"])

    def test_token_is_read_without_modifying_environment_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            env = home / ".env"
            env.write_text("OTHER=x\nTELEGRAM_BOT_TOKEN='secret-token'\n")
            before = env.read_bytes()
            self.assertEqual(token_for_home(home), "secret-token")
            self.assertEqual(env.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
