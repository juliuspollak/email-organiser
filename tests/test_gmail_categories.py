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

    def test_real_marketing_examples_without_list_headers(self):
        cases=[
            ("Packt Publishing <offers@packt.example>","didn't want you to miss this one!"),
            ("Photobook Worldwide <news@photo.example>","Your weekend project is here"),
            ("MASH MX-MTB Outlet <news@mash.example>","New 2027 Leatt Riding Gear is Here!"),
            ("Shop <hello@shop.example>","Your cart is saved on Shop"),
            ("My Tesla Accessories <hello@tesla.example>","Your YL sunshade has landed"),
        ]
        opts,mode=current_options()
        for sender,subject in cases:
            with self.subTest(sender=sender):
                item=msg(subject,["INBOX"],sender=sender)
                cat,_,_,_=classify(item,PREF)
                self.assertEqual(cat,"Promotions")
                self.assertEqual(proposed_operation(item,cat,mode,{},opts),
                                 ("move","EO Promotions"))

    def test_merchant_transaction_is_protected(self):
        item=msg("Your order confirmation and receipt",["INBOX"],
                 sender="Shop <receipts@shop.example>")
        cat,_,_,_=classify(item,PREF)
        self.assertIn(cat,("Purchases","Transactions"))
        opts,mode=current_options()
        self.assertIsNone(proposed_operation(item,cat,mode,{},opts))

    def test_public_service_update_not_automatically_promoted(self):
        item=msg("Help to reduce healthcare costs",["INBOX"],
                 sender="Service NSW <notifications@service.example>")
        cat,_,_,_=classify(item,PREF)
        self.assertEqual(cat,"Review")

    def test_obfuscated_subject_cannot_be_moved(self):
        item=msg("Introducing"+chr(0x200c)+" new gear",["INBOX","CATEGORY_PROMOTIONS"])
        cat,_,_,_=classify(item,PREF)
        opts,mode=current_options()
        self.assertIsNone(proposed_operation(item,cat,mode,{},opts))

if __name__=="__main__":
    unittest.main()
