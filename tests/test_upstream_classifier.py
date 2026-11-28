"""Contract tests for actual CleanYourMail source integrated into the organiser."""
import unittest

from organiser import classify, proposed_operation, upstream_classification, current_options, flags

PREF={"senders":{},"domains":{}}

def email(subject, *, sender="Retail Outlet <offers@retailer.example>",
          list_id="", feedback_id="", unsubscribe="", authentication=""):
    headers={
        "list-id":list_id,
        "feedback-id":feedback_id,
        "list-unsubscribe":unsubscribe,
        "authentication-results":authentication,
    }
    return {"id":"123","sender":sender,"subject":subject,
            "flags":flags(headers),"raw_headers":headers,"age_days":20}

class CleanYourMailIntegration(unittest.TestCase):
    def setUp(self):
        self.opts,self.mode=current_options()

    def test_upstream_detects_bulk_promotion_and_moves(self):
        message=email("Limited time offer: 40% off",
                      unsubscribe="<https://example.com/unsubscribe>",
                      feedback_id="newsletter:example.com")
        upstream=upstream_classification(message)
        self.assertIsNotNone(upstream)
        self.assertTrue(upstream.bulk)
        self.assertGreaterEqual(upstream.confidence,50)
        category,*_=classify(message,PREF)
        self.assertEqual(category,"Promotions")
        self.assertEqual(proposed_operation(message,category,self.mode,{},self.opts),
                         ("move","EO Promotions"))

    def test_upstream_protects_invoice_even_with_campaign_headers(self):
        message=email("Your invoice and payment are due",
                      unsubscribe="<https://example.com/unsubscribe>",
                      feedback_id="campaign")
        upstream=upstream_classification(message)
        self.assertTrue(upstream.protected)
        category,*_=classify(message,PREF)
        self.assertEqual(category,"Transactions")
        self.assertIsNone(proposed_operation(message,category,self.mode,{},self.opts))

    def test_upstream_protection_vetoes_ambiguous_marketing(self):
        message=email("Special offer for your account",
                      unsubscribe="<https://example.com/unsubscribe>")
        upstream=upstream_classification(message)
        self.assertTrue(upstream.protected)
        category,*_=classify(message,PREF)
        self.assertIsNone(proposed_operation(message,category,self.mode,{},self.opts))

    def test_missing_headers_cannot_activate_upstream(self):
        message=email("Unrecognised text")
        message.pop("raw_headers")
        self.assertIsNone(upstream_classification(message))

    def test_upstream_newsletter_scoring(self):
        message=email("Monthly bulletin",
                      list_id="mail.example.com",
                      unsubscribe="<https://example.com/unsubscribe>")
        upstream=upstream_classification(message)
        self.assertEqual(upstream.category,"newsletter")
        self.assertGreaterEqual(upstream.confidence,50)
        category,*_=classify(message,PREF)
        self.assertEqual(category,"Newsletters")
        self.assertEqual(proposed_operation(message,category,self.mode,{},self.opts),
                         ("move","EO Review"))

if __name__=="__main__":
    unittest.main()
