"""Gmail native classifications take priority after protective subject rules."""
import unittest
from organiser import classify, proposed_operation, flags, current_options

def msg(subject, labels, sender="Offers <offers@example.com>"):
    return {
      "id":"msg-1", "sender":sender, "subject":subject,
      "gmail_labels":labels,
      "flags":flags({}), "age_days":10
    }

PREF={"senders":{},"domains":{}}

class GmailNativeCategoryTests(unittest.TestCase):
    def test_unrecognised_promotional_subject_uses_gmail_category(self):
        item=msg("Introducing the TORQUE Max",["INBOX","CATEGORY_PROMOTIONS"])
        category,_,_,_=classify(item,PREF)
        self.assertEqual(category,"Promotions")
        opts,mode=current_options()
        self.assertEqual(proposed_operation(item,category,mode,{},opts),
                         ("move","EO Promotions"))

    def test_promotion_not_from_gmail_category_stays_review(self):
        item=msg("Introducing the TORQUE Max",["INBOX","CATEGORY_PERSONAL"])
        cat,_,_,_=classify(item,PREF)
        self.assertEqual(cat,"Review")
        opts,mode=current_options()
        self.assertIsNone(proposed_operation(item,cat,mode,{},opts))

    def test_financial_subject_protected_despite_promotions(self):
        item=msg("Your invoice and payment due",["INBOX","CATEGORY_PROMOTIONS"])
        cat,_,_,_=classify(item,PREF)
        self.assertEqual(cat,"Transactions")
        opts,mode=current_options()
        self.assertIsNone(proposed_operation(item,cat,mode,{},opts))

    def test_password_reset_protected_despite_promotions(self):
        item=msg("Your password reset request",["INBOX","CATEGORY_PROMOTIONS"])
        cat,_,_,_=classify(item,PREF)
        self.assertEqual(cat,"Important")
        opts,mode=current_options()
        self.assertIsNone(proposed_operation(item,cat,mode,{},opts))

    def test_gmail_automatic_important_mark_does_not_block_promotion(self):
        item=msg("Introducing a new product",["INBOX","IMPORTANT","CATEGORY_PROMOTIONS"])
        cat,_,_,_=classify(item,PREF)
        self.assertEqual(cat,"Promotions")
        opts,mode=current_options()
        self.assertEqual(proposed_operation(item,cat,mode,{},opts),
                         ("move","EO Promotions"))

    def test_user_starred_promotion_stays(self):
        item=msg("Introducing a new product",["INBOX","STARRED","CATEGORY_PROMOTIONS"])
        cat,_,_,_=classify(item,PREF)
        opts,mode=current_options()
        self.assertIsNone(proposed_operation(item,cat,mode,{},opts))

    def test_obfuscated_subject_cannot_be_moved(self):
        item=msg("Introducing"+chr(0x200c)+" new gear",["INBOX","CATEGORY_PROMOTIONS"])
        cat,_,_,_=classify(item,PREF)
        opts,mode=current_options()
        self.assertIsNone(proposed_operation(item,cat,mode,{},opts))

if __name__=="__main__":
    unittest.main()
