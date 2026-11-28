"""Simple-mode settings preserve old installs and prohibit destructive actions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import organiser


class SimpleOptionsTests(unittest.TestCase):
    def read_options(self, json_text):
        with tempfile.TemporaryDirectory() as directory:
            options=Path(directory,"options.json")
            options.write_text(json_text)
            with patch.object(organiser,"OPTS",options):
                return organiser.current_options()

    def test_one_switch_on_enables_bulk_reversible_filing(self):
        opts,mode=self.read_options('{"organise_mail": true}')
        self.assertEqual(mode,"safe")
        self.assertTrue(opts["enable_actions"])
        self.assertTrue(opts["bulk_backlog_enabled"])
        self.assertTrue(opts["auto_file_marketing"])
        self.assertEqual(opts["max_actions_per_run"],50)
        self.assertEqual(opts["max_per_account"],500)
        self.assertEqual(opts["history_days"],3650)
        self.assertEqual(opts["interval_minutes"],15)
        self.assertFalse(opts["trash_old_delivery_updates"])
        self.assertFalse(opts["enable_unsubscribe"])

    def test_one_switch_off_never_mutates_mail(self):
        opts,mode=self.read_options('{"organise_mail": false}')
        self.assertEqual(mode,"dry_run")
        self.assertFalse(opts["enable_actions"])

    def test_migrated_install_remains_enabled(self):
        opts,mode=self.read_options('{"mode":"safe","enable_actions":true,"max_actions_per_run":10}')
        self.assertEqual(mode,"safe")
        self.assertEqual(opts["max_actions_per_run"],50)

    def test_migrated_disabled_install_remains_disabled(self):
        opts,mode=self.read_options('{"mode":"dry_run","enable_actions":true}')
        self.assertEqual(mode,"dry_run")
        self.assertFalse(opts["enable_actions"])

    def test_old_destructive_flags_cannot_override_simple_mode(self):
        opts,mode=self.read_options(
            '{"organise_mail":true,"mode":"balanced","enable_unsubscribe":true,'
            '"trash_old_delivery_updates":true,"expanded_actions_enabled":true}'
        )
        self.assertEqual(mode,"safe")
        self.assertFalse(opts["enable_unsubscribe"])
        self.assertFalse(opts["trash_old_delivery_updates"])
        self.assertFalse(opts["expanded_actions_enabled"])


if __name__ == "__main__":
    unittest.main()
