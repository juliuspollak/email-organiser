"""Offline tests for rotating Gmail/Yahoo backlog processing."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import organiser


class FakeResponse:
    def __init__(self, result):
        self.result = result
    def raise_for_status(self):
        return None
    def json(self):
        return self.result


class FakeGmailSession:
    def __init__(self):
        self.headers = {}
        self.list_requests = []
    def get(self, url, params=None, timeout=30):
        if url.endswith("/profile"):
            return FakeResponse({"emailAddress": "me@example.com"})
        if url.endswith("/messages"):
            self.list_requests.append(params.copy())
            ids=["m1","m2"] if "pageToken" not in params else ["m3"]
            result={"messages":[{"id":i} for i in ids]}
            if "pageToken" not in params:
                result["nextPageToken"]="second"
            return FakeResponse(result)
        ident=url.rsplit("/",1)[-1]
        return FakeResponse({"id":ident,"payload":{"headers":[
            {"name":"From","value":"Offers <offers@example.com>"},
            {"name":"Subject","value":"Discount available"}
        ]},"internalDate":"1000000000000"})


class FakeYahooScan:
    def __init__(self):
        self.capabilities=(b"IMAP4rev1",)
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def login(self,*args):
        return "OK",[b"Logged in"]
    def select(self,*args,**kwargs):
        return "OK",[b"5"]
    def uid(self,operation,*args):
        if operation=="SEARCH":
            return "OK",[b"1 2 3 4 5"]
        if operation=="FETCH":
            return "OK",[(b"header",b"From: Offers <offers@example.com>\r\nSubject: A deal\r\n\r\n")]
        raise AssertionError(operation)


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        (self.base/"gmail_token.json").write_text("{}")
        (self.base/"yahoo_app_password.txt").write_text("dummy")
        self.base_patch=patch.object(organiser,"BASE",self.base)
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)

    def test_gmail_rotates_pages(self):
        class ValidCred:
            scopes=[organiser.GMAIL_MODIFY_SCOPE]
            valid=True
            token="fake-access-token"
        session=FakeGmailSession()
        with patch.object(organiser.Credentials,"from_authorized_user_file",return_value=ValidCred()),patch.object(
            organiser.requests,"Session",return_value=session):
            first=organiser.gmail_messages({"gmail_email":"me@example.com"},3650,2,backlog=True)
            self.assertEqual([m["id"] for m in first],["m1","m2"])
            self.assertEqual(json.loads((self.base/"backlog_progress.json").read_text())["gmail"],1)
            second=organiser.gmail_messages({"gmail_email":"me@example.com"},3650,2,backlog=True)
            self.assertEqual([m["id"] for m in second],["m3"])
            self.assertEqual(json.loads((self.base/"backlog_progress.json").read_text())["gmail"],0)
            third=organiser.gmail_messages({"gmail_email":"me@example.com"},3650,2,backlog=True)
            self.assertEqual([m["id"] for m in third],["m1","m2"])
        self.assertIn("pageToken",session.list_requests[2])

    def test_yahoo_rotates_backlog_without_network(self):
        with patch.object(organiser.imaplib,"IMAP4_SSL",return_value=FakeYahooScan()):
            one=organiser.yahoo_messages({"yahoo_email":"me@yahoo.com"},3650,2,backlog=True)
            two=organiser.yahoo_messages({"yahoo_email":"me@yahoo.com"},3650,2,backlog=True)
            three=organiser.yahoo_messages({"yahoo_email":"me@yahoo.com"},3650,2,backlog=True)
            four=organiser.yahoo_messages({"yahoo_email":"me@yahoo.com"},3650,2,backlog=True)
        self.assertEqual([m["id"] for m in one],["5","4"])
        self.assertEqual([m["id"] for m in two],["3","2"])
        self.assertEqual([m["id"] for m in three],["1"])
        self.assertEqual([m["id"] for m in four],["5","4"])
        self.assertEqual(json.loads((self.base/"backlog_progress.json").read_text())["yahoo"],1)

    def test_backlog_disabled_does_not_write_progress(self):
        with patch.object(organiser.imaplib,"IMAP4_SSL",return_value=FakeYahooScan()):
            msgs=organiser.yahoo_messages({"yahoo_email":"me@yahoo.com"},30,2,backlog=False)
        self.assertEqual([m["id"] for m in msgs],["5","4"])
        self.assertFalse((self.base/"backlog_progress.json").exists())


if __name__ == "__main__":
    unittest.main()
