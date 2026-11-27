"""Safety regression tests for opt-in filing. No network or mailbox access."""
import unittest

from organiser import proposed_operation


def message(sender="offers@example.com", list_mail=True):
    return {
        "id": "123",
        "sender": sender,
        "subject": "Save 20% today",
        "flags": {"list": list_mail, "unsub": False},
        "age_days": 1,
    }


class FilingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.opts = {
            "enable_actions": True,
            "auto_file_marketing": True,
            "trash_old_delivery_updates": False,
            "_protected_marketing_senders": set(),
        }

    def test_dry_run_never_moves(self):
        self.assertIsNone(proposed_operation(message(), "Promotions", "dry_run", {}, self.opts))

    def test_disabled_actions_never_move(self):
        self.opts["enable_actions"] = False
        self.assertIsNone(proposed_operation(message(), "Promotions", "safe", {}, self.opts))

    def test_auto_marketing_disabled_by_default(self):
        self.opts["auto_file_marketing"] = False
        self.assertIsNone(proposed_operation(message(), "Promotions", "safe", {}, self.opts))

    def test_marketing_list_can_move(self):
        self.assertEqual(
            proposed_operation(message(), "Promotions", "safe", {}, self.opts),
            ("move", "EO Promotions"),
        )

    def test_newsletter_list_can_move(self):
        self.assertEqual(
            proposed_operation(message(), "Newsletters", "safe", {}, self.opts),
            ("move", "EO Review"),
        )

    def test_no_list_evidence_no_auto_move(self):
        self.assertIsNone(proposed_operation(message(list_mail=False), "Promotions", "safe", {}, self.opts))

    def test_mixed_sender_protected(self):
        self.opts["_protected_marketing_senders"] = {"offers@example.com"}
        self.assertIsNone(proposed_operation(message(), "Promotions", "safe", {}, self.opts))

    def test_bulk_mode_files_individual_marketing_from_mixed_sender(self):
        self.opts["_protected_marketing_senders"] = {"offers@example.com"}
        self.opts["bulk_backlog_enabled"] = True
        self.assertEqual(
            proposed_operation(message(), "Promotions", "safe", {}, self.opts),
            ("move", "EO Promotions"),
        )

    def test_bulk_mode_still_protects_non_marketing_message(self):
        self.opts["bulk_backlog_enabled"] = True
        for category in ("Important", "Transactions", "Suspicious", "Purchase Issues", "Review"):
            with self.subTest(category=category):
                self.assertIsNone(proposed_operation(message(),category,"safe",{},self.opts))

    def test_bulk_mode_rejects_obfuscated_marketing_subject(self):
        self.opts["bulk_backlog_enabled"] = True
        msg=message()
        msg["subject"]="D"+chr(0x200c)+"iscount offer"
        self.assertIsNone(proposed_operation(msg,"Promotions","safe",{},self.opts))

    def test_important_and_transactions_never_auto_move(self):
        for category in ("Important", "Transactions", "Purchase Issues", "Suspicious", "Review", "Purchases"):
            with self.subTest(category=category):
                self.assertIsNone(proposed_operation(message(), category, "safe", {}, self.opts))

    def test_explicit_approved_purchase_still_works(self):
        self.assertEqual(
            proposed_operation(
                message(sender="auto-confirm@amazon.com.au", list_mail=False),
                "Purchases", "safe",
                {"auto-confirm@amazon.com.au": {"Purchases"}},
                self.opts,
            ),
            ("move", "EO Purchases"),
        )

    def test_explicit_approved_delivery_still_works(self):
        self.assertEqual(
            proposed_operation(
                message(sender="order-update@amazon.com.au", list_mail=False),
                "Delivery Updates", "safe",
                {"order-update@amazon.com.au": {"Delivery Updates"}},
                self.opts,
            ),
            ("move", "EO Low Priority"),
        )


if __name__ == "__main__":
    unittest.main()
