"""Home Assistant Start/Stop is the only control; old flags cannot turn on deletion."""
import unittest
import organiser

class AppDefaultsTests(unittest.TestCase):
    def test_runs_automatically(self):
        opts,mode=organiser.current_options()
        self.assertEqual(mode,"safe")
        self.assertTrue(opts["enable_actions"])
        self.assertTrue(opts["auto_file_marketing"])
        self.assertTrue(opts["bulk_backlog_enabled"])
        self.assertEqual(opts["max_actions_per_run"],50)
        self.assertEqual(opts["max_per_account"],500)
        self.assertEqual(opts["history_days"],3650)

    def test_no_destructive_features(self):
        opts,_=organiser.current_options()
        self.assertFalse(opts["enable_unsubscribe"])
        self.assertFalse(opts["trash_old_delivery_updates"])
        self.assertFalse(opts["expanded_actions_enabled"])

if __name__ == "__main__":
    unittest.main()
