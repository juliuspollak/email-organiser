"""Yahoo IMAP behavioural tests without real credentials or network calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import organiser


class FakeYahoo:
    capabilities = (b"IMAP4rev1", b"MOVE")
    def __init__(self, missing=False):
        self.folder = None
        self.missing = missing
        self.created = False
        self.moves = []
        self.list_calls = 0
        self.selection_history = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def login(self, user, password):
        return ("OK", [b"Logged in"])
    def select(self, folder, readonly=False):
        self.selection_history.append((folder, readonly))
        if folder == '"EO Promotions"' and self.missing and not self.created:
            return ("NO", [b"Missing"])
        self.folder = folder
        return ("OK", [b"1"])
    def create(self, folder):
        self.created = True
        return ("OK", [b"Created"])
    def list(self, *args):
        self.list_calls += 1
        raise AssertionError("LIST must not be called")
    def uid(self, operation, *args):
        if operation == "SEARCH":
            assert self.folder == "INBOX"
            return ("OK", [b"123"])
        if operation == "FETCH":
            assert self.folder == "INBOX"
            return ("OK", [(b"123 (FLAGS (\\Seen))", b""), b")"])
        if operation == "MOVE":
            assert self.folder == "INBOX"
            self.moves.append((args[0],args[1]))
            return ("OK", [b"Moved"])
        raise AssertionError(operation)


class YahooMoveTests(unittest.TestCase):
    def run_case(self, missing):
        fake=FakeYahoo(missing=missing)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "yahoo_app_password.txt").write_text("dummy")
            with patch.object(organiser, "BASE", Path(directory)), patch.object(
                organiser.imaplib, "IMAP4_SSL", return_value=fake
            ):
                outcome=organiser.yahoo_apply_batch(
                    {"yahoo_email": "example@yahoo.com"},
                    [({"id": "123", "sender": "offers@example.com"}, "Promotions", ("move", "EO Promotions"))],
                )
        self.assertEqual(outcome[0][3], "moved")
        self.assertEqual(fake.list_calls, 0)
        self.assertEqual(fake.moves, [(b"123", '"EO Promotions"')])
        self.assertIn(('"EO Promotions"', True), fake.selection_history)
        self.assertIn(("INBOX", False), fake.selection_history)
        self.assertEqual(fake.created, missing)

    def test_existing_folder_moves_without_list(self):
        self.run_case(False)

    def test_missing_folder_created_then_moves_without_list(self):
        self.run_case(True)


if __name__ == "__main__":
    unittest.main()
