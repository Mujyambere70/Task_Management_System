"""Shared test helpers.

The tests run on SQLite by default. To run the same tests on PostgreSQL:
    TEST_DATABASE_URL=postgresql://user@host:5432/dbname python -m unittest discover tests
(the database is emptied before every test - use a database only for tests!)
"""

import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

PG_URL = os.environ.get("TEST_DATABASE_URL", "")
DRIVER = None
if PG_URL:
    try:
        import psycopg as DRIVER                      # the real driver, when installed
    except ImportError:
        import pgmini as DRIVER                       # small built-in client for tests


class FakeNotifier:
    """Catches emails instead of sending them."""
    def __init__(self, provider="Test mail"):
        self.provider, self.sent = provider, []

    def email_provider(self):
        return self.provider

    def send_email(self, to, subject, text, attachment=None):
        self.sent.append((to, subject, text))
        return "sent"

    def deliver(self, jobs, on_result):
        for nid, _channel, to, subject, text in jobs:
            self.sent.append((to, subject, text))
            on_result(nid, "sent")


_TEMPLATE = None


def target(seeded=False):
    """A new SQLite file (a copy of one seeded file, which is much faster), or an emptied PostgreSQL database."""
    global _TEMPLATE
    if not PG_URL:
        path = os.path.join(tempfile.mkdtemp(), "test.db")
        if seeded:
            if _TEMPLATE is None:
                from store import Store
                from demo_data import load_demo
                _TEMPLATE = os.path.join(tempfile.mkdtemp(), "template.db")
                load_demo(Store(_TEMPLATE, notifier=FakeNotifier(), env={}))
            shutil.copyfile(_TEMPLATE, path)
        return path
    conn = DRIVER.connect(PG_URL)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")
    conn.commit()
    conn.close()
    return PG_URL


class Base(unittest.TestCase):
    seed = True
    env = {}

    def setUp(self):
        from app import create_app
        self.mail = FakeNotifier()
        self.app = create_app(target(self.seed), seed=self.seed, testing=True, notifier=self.mail, driver=DRIVER,
                              env=dict(self.env))
        self.store = self.app.store
        self.c = self.app.test_client()

    # ---------------------------------------------------------------- helpers
    def token(self, client=None):
        client = client or self.c
        with client.session_transaction() as s:
            if "csrf" not in s:
                s["csrf"] = "t0ken" * 4
            return s["csrf"]

    def post(self, url, data=None, client=None, **kw):
        client = client or self.c
        data = dict(data or {})
        data["csrf_token"] = self.token(client)
        return client.post(url, data=data, **kw)

    def post_json(self, url, payload, client=None):
        client = client or self.c
        return client.post(url, json=payload, headers={"X-CSRF-Token": self.token(client)})

    def login(self, email="admin@taskflow.demo", password="Admin@1234", client=None):
        return self.post("/login", {"email": email, "password": password}, client=client)

    def new_client(self, email=None, password="Demo@1234"):
        c = self.app.test_client()
        if email:
            self.login(email, password, client=c)
        return c

    def uid(self, email):
        return self.store.user_by_email(email)["id"]

    def ws_of(self, email):
        return self.store.memberships(self.uid(email))

    def html(self, url, client=None):
        r = (client or self.c).get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.get_data(as_text=True)

    def first_ws(self):
        return next(w["id"] for w in self.store.memberships(self.uid("admin@taskflow.demo"))
                    if w["name"] == "Jean's Dev Studio")

    def second_ws(self):
        return next(w["id"] for w in self.store.memberships(self.uid("admin@taskflow.demo"))
                    if w["name"] == "SFU Data Analysis Group")

def csrf_from(html):
    return re.search(r'name="csrf-token" content="(\w+)"', html).group(1)
