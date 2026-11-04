"""
tools/collect_strings.py - finds every English text of the interface that needs a translation.

It opens all pages (as the admin, a member and a visitor) in a "recording" language and writes
the texts that the page translator meets into tools/strings_en.json. Texts from Python code
(messages, emails, live events) are added from the source files.
Run:  python tools/collect_strings.py
"""

import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import i18n  # noqa: E402

seen = []
_orig = i18n.translate_text


MISSING = "--missing" in sys.argv          # list only the texts that the catalog does not translate yet
LANG = next((a.split("=")[1] for a in sys.argv if a.startswith("--lang=")), "ru")


def record(text, lang=None):
    key = " ".join(str(text or "").split())
    if MISSING:
        done = _orig(text, LANG)
        if done != text:
            return done
    if key and re.search(r"[A-Za-z]{2}", key) and key not in seen:
        seen.append(key)
    return text


i18n.translate_text = record
if not MISSING:
    i18n.catalog = lambda lang: ({"__record__": "x"}, [])

from support import FakeNotifier  # noqa: E402
from app import create_app  # noqa: E402

app = create_app(os.path.join(tempfile.mkdtemp(), "c.db"), testing=True, notifier=FakeNotifier(), env={})
store = app.store


def client(email=None, password="Demo@1234"):
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = "x" * 32
        s["lang"] = LANG
    if email:
        c.post("/login", data={"email": email, "password": password, "csrf_token": "x" * 32})
        with c.session_transaction() as s:
            s["lang"] = LANG
    return c


guest = client()
for url in ["/login", "/register", "/forgot", "/reset/x", "/about", "/explore", "/explore?q=zzz",
            "/r/jean-michel-mujyambere", "/r/fatima-haddad", "/r/li-wei", "/r/anna-petrova", "/nope"]:
    guest.get(url)
code = store.memberships(1)[0]["invite_code"]
guest.get("/join/" + code)
guest.get("/register?invite=" + code)

admin = client("admin@taskflow.demo", "Admin@1234")
ws = store.memberships(1)
tasks = store.tasks(ws[0]["id"])
pages = ["/", "/board", "/tasks", "/tasks?status=open", "/tasks/new", "/calendar", "/projects", "/team", "/reports",
         "/reports?metric=hours", "/profile", "/notifications", "/workspaces", "/workspace/settings", "/admin",
         "/admin?tab=data", "/admin?tab=maintenance", "/resumes", "/resumes/1/edit", "/r/jean-michel-mujyambere",
         "/r/jean-michel-ru", "/explore", "/about", "/tasks/99999", "/join/" + code]
pages += [f"/tasks/{t['id']}" for t in tasks[:40]] + [f"/tasks/{tasks[0]['id']}/edit"]
for url in pages:
    admin.get(url)
admin.post("/tasks/" + str(tasks[0]["id"]) + "/timer", data={"csrf_token": "x" * 32})
admin.get("/")
member = client("anna@taskflow.demo")
for url in ["/", "/team", "/profile", "/resumes", "/r/anna-petrova", "/admin", "/workspace/settings"]:
    member.get(url)
fresh = client()
fresh.post("/register", data={"name": "Empty Person", "email": "empty@teamtest.io", "password": "Secret123",
                              "confirm": "Secret123", "csrf_token": "x" * 32})
with fresh.session_transaction() as s:
    s["lang"] = "ru"
for url in ["/", "/board", "/tasks", "/projects", "/reports", "/resumes", "/notifications", "/team", "/calendar"]:
    fresh.get(url)
fresh.post("/login/verify")

if MISSING:
    print("\n".join(seen))
    sys.exit(0)

# texts from the Python code: messages, errors, emails, live events, resume headings
pattern = re.compile(r'(?:TaskError|flash|tr|translate_text|_t|t)\(\s*(f?)"((?:[^"\\]|\\.)+)"')
for name in os.listdir(ROOT):
    if name.endswith(".py"):
        src = open(os.path.join(ROOT, name), encoding="utf-8").read()
        for f, text in pattern.findall(src):
            text = text.replace('\\"', '"')
            if f:
                text = re.sub(r"\{[^}]+\}", "{0}", text, count=1)
                text = re.sub(r"\{[^}0-9][^}]*\}", "{1}", text, count=1)
            record(text)
import store as store_mod  # noqa: E402
import resumes  # noqa: E402
import resume_views  # noqa: E402
for text in store_mod.LIVE.values():
    record(text)
for title, fields in resumes.SECTION_TYPES.values():
    record(title)
    for _k, label, _kind in fields:
        record(label)
for d in (resumes.THEMES, resumes.FONTS, resumes.VISIBILITY):
    for v in d.values():
        record(v)
for s in resume_views.EDITOR_STRINGS + ["Profile", "Present", "This resume is still empty.", "Native", "Fluent",
                                        "Proficient", "Intermediate", "Basic", "Beginner", "Key achievements"]:
    record(s)
for n in list(store_mod.STATUS_NAMES.values()) + list(store_mod.PRIORITY_NAMES.values()):
    record(n)

out = os.path.join(HERE, "strings_en.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(seen, f, ensure_ascii=False, indent=0)
print(len(seen), "texts ->", out)
