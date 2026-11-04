"""
resumes.py - the resume (CV) builder.

Every person can make one or more resumes: a header (name, headline, photo, contacts, links),
a summary and any number of sections (experience, education, projects, skills, certificates,
languages, awards, interests, references, free text or sections with an own title).
Sections can be reordered and hidden. A resume has a design (3 layouts, any accent color,
3 fonts), a language for its headings, and a visibility:

    private  only the owner sees it
    link     anyone with the link sees it (not listed)
    public   also listed on the "Explore resumes" page

The content is stored as one JSON document per resume and checked here before saving.
"""

import base64
import io
import json
import re
import secrets
import unicodedata

from core import TaskError, now

THEMES = {"modern": "Modern (side column)", "classic": "Classic (one column)", "compact": "Compact (two columns)"}
FONTS = {"inter": "Inter (sans-serif)", "serif": "Merriweather (serif)", "slab": "Roboto Slab"}
VISIBILITY = {"private": "Only me", "link": "Anyone with the link", "public": "Public - listed in Explore"}
RESERVED = {"new", "explore", "edit", "import", "admin", "api", "static", "photo", "print"}

# section type -> default title and the fields of one item (key, label, kind)
SECTION_TYPES = {
    "experience": ("Experience", [("role", "Job title", "text"), ("org", "Company / organisation", "text"),
                                  ("place", "City, country", "text"), ("start", "Start", "text"),
                                  ("end", "End (or Present)", "text"), ("link", "Link", "url"),
                                  ("bullets", "What you did (one point per line)", "lines")]),
    "education": ("Education", [("degree", "Degree / programme", "text"), ("school", "School / university", "text"),
                                ("place", "City, country", "text"), ("start", "Start", "text"),
                                ("end", "End (or Present)", "text"), ("bullets", "Details (one point per line)", "lines")]),
    "projects": ("Projects", [("name", "Project name", "text"), ("tech", "Technologies", "text"),
                              ("link", "Link", "url"), ("start", "Start", "text"), ("end", "End (or Present)", "text"),
                              ("bullets", "What it does (one point per line)", "lines")]),
    "skills": ("Skills", [("group", "Group (e.g. Programming)", "text"),
                          ("items", "Skills, separated by commas", "text")]),
    "certifications": ("Certifications", [("name", "Certificate", "text"), ("issuer", "Issued by", "text"),
                                          ("date", "Date", "text"), ("link", "Link", "url")]),
    "languages": ("Languages", [("name", "Language", "text"), ("level", "Level (e.g. Native, B2)", "text"),
                                ("score", "Level bar (1-5)", "level")]),
    "awards": ("Achievements", [("name", "Achievement / award", "text"), ("date", "Date", "text"),
                                ("bullets", "Details (one point per line)", "lines")]),
    "interests": ("Interests", [("name", "Interest", "text")]),
    "references": ("References", [("name", "Name", "text"), ("role", "Position", "text"),
                                  ("contact", "Email or phone", "text")]),
    "custom": ("My section", [("title", "Title", "text"), ("subtitle", "Subtitle", "text"), ("date", "Date", "text"),
                              ("link", "Link", "url"), ("bullets", "Details (one point per line)", "lines")]),
    "text": ("About me", []),
}
MAX_SECTIONS, MAX_ITEMS, MAX_LINKS = 20, 30, 8
TEXT_LIMIT, LINES_LIMIT, BODY_LIMIT = 200, 2500, 3000
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38})[a-z0-9]$")


def _text(v, limit=TEXT_LIMIT):
    return " ".join(str(v or "").split())[:limit]


def _lines(v, limit=LINES_LIMIT):
    rows = [" ".join(line.split()) for line in str(v or "").replace("\r", "").split("\n")]
    return "\n".join(r for r in rows if r)[:limit]


def _url(v):
    v = str(v or "").strip()[:300]
    if not v:
        return ""
    if v.startswith(("http://", "https://", "mailto:")):
        return v
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", v, re.I):     # github.com/me -> https://github.com/me
        return "https://" + v
    raise TaskError("A link must start with https:// (for example https://github.com/yourname).")


def slugify(text):
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:36]
    return s if len(s) >= 3 else ""


def new_section(kind, title=None):
    return {"id": secrets.token_hex(4), "type": kind, "title": title or SECTION_TYPES[kind][0], "visible": True,
            "items": [], "body": ""}


def blank_resume(name="", headline="", email=""):
    return {"name": name, "headline": headline, "email": email, "phone": "", "location": "", "website": "",
            "links": [], "summary": "",
            "sections": [new_section(k) for k in ("experience", "education", "projects", "skills", "languages")]}


def clean_resume_data(raw):
    """Check and clean the JSON sent by the editor. Raises TaskError with a clear message."""
    if not isinstance(raw, dict):
        raise TaskError("The resume data is not valid.")
    d = {k: _text(raw.get(k)) for k in ("name", "headline", "phone", "location")}
    d["email"] = _text(raw.get("email"), 120)
    if d["email"] and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", d["email"]):
        raise TaskError("Please enter a valid email address.")
    d["website"] = _url(raw.get("website"))
    if len(d["name"]) < 2:
        raise TaskError("Please write your name at the top of the resume.")
    d["summary"] = _lines(raw.get("summary"), BODY_LIMIT)
    links = raw.get("links") or []
    if not isinstance(links, list) or len(links) > MAX_LINKS:
        raise TaskError("A resume can have at most 8 links.")
    d["links"] = []
    for link in links:
        if isinstance(link, dict) and (link.get("url") or link.get("label")):
            d["links"].append({"label": _text(link.get("label"), 40), "url": _url(link.get("url"))})
    sections = raw.get("sections") or []
    if not isinstance(sections, list) or len(sections) > MAX_SECTIONS:
        raise TaskError("A resume can have at most 20 sections.")
    d["sections"] = []
    for s in sections:
        if not isinstance(s, dict) or s.get("type") not in SECTION_TYPES:
            raise TaskError("Unknown section type.")
        kind = s["type"]
        fields = SECTION_TYPES[kind][1]
        items = s.get("items") or []
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            raise TaskError("A section can have at most 30 entries.")
        clean_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            c = {}
            for key, _label, ftype in fields:
                v = it.get(key)
                if ftype == "lines":
                    c[key] = _lines(v)
                elif ftype == "url":
                    c[key] = _url(v)
                elif ftype == "level":
                    try:
                        c[key] = max(0, min(5, int(v or 0)))
                    except (TypeError, ValueError):
                        c[key] = 0
                else:
                    c[key] = _text(v, 400 if key == "items" else TEXT_LIMIT)
            if any(c.values()):
                clean_items.append(c)
        sid = re.sub(r"[^a-z0-9]", "", str(s.get("id") or ""))[:12] or secrets.token_hex(4)
        d["sections"].append({"id": sid, "type": kind, "title": _text(s.get("title"), 60) or SECTION_TYPES[kind][0],
                              "visible": bool(s.get("visible", True)), "items": clean_items,
                              "body": _lines(s.get("body"), BODY_LIMIT) if kind == "text" else ""})
    return d


def from_json_resume(j):
    """Convert the open "JSON Resume" format (jsonresume.org) into our resume data."""
    b = j.get("basics") or {}
    loc = b.get("location") or {}
    d = {"name": b.get("name", ""), "headline": b.get("label", ""), "email": b.get("email", ""),
         "phone": b.get("phone", ""), "website": b.get("url") or b.get("website") or "",
         "location": ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("countryCode")) if x),
         "summary": b.get("summary", ""),
         "links": [{"label": p.get("network", ""), "url": p.get("url", "")} for p in b.get("profiles") or []][:MAX_LINKS],
         "sections": []}

    def add(kind, items):
        if items:
            s = new_section(kind)
            s["items"] = items[:MAX_ITEMS]
            d["sections"].append(s)

    add("experience", [{"role": w.get("position", ""), "org": w.get("name") or w.get("company", ""),
                        "place": w.get("location", ""), "start": w.get("startDate", ""),
                        "end": w.get("endDate", "") or "Present", "link": w.get("url", ""),
                        "bullets": "\n".join(([w["summary"]] if w.get("summary") else []) + (w.get("highlights") or []))}
                       for w in j.get("work") or []])
    add("education", [{"degree": " ".join(x for x in (e.get("studyType"), e.get("area")) if x),
                       "school": e.get("institution", ""), "start": e.get("startDate", ""), "end": e.get("endDate", ""),
                       "bullets": "\n".join(e.get("courses") or [])} for e in j.get("education") or []])
    add("projects", [{"name": p.get("name", ""), "tech": ", ".join(p.get("keywords") or []), "link": p.get("url", ""),
                      "start": p.get("startDate", ""), "end": p.get("endDate", ""),
                      "bullets": "\n".join(([p["description"]] if p.get("description") else []) + (p.get("highlights") or []))}
                     for p in j.get("projects") or []])
    add("skills", [{"group": s.get("name", ""), "items": ", ".join(s.get("keywords") or [])} for s in j.get("skills") or []])
    add("certifications", [{"name": c.get("name", ""), "issuer": c.get("issuer", ""), "date": c.get("date", ""),
                            "link": c.get("url", "")} for c in j.get("certificates") or []])
    add("languages", [{"name": x.get("language", ""), "level": x.get("fluency", ""), "score": 0}
                      for x in j.get("languages") or []])
    add("awards", [{"name": a.get("title", ""), "date": a.get("date", ""), "bullets": a.get("summary", "")}
                   for a in j.get("awards") or []])
    add("interests", [{"name": i.get("name", "")} for i in j.get("interests") or []])
    return d


class ResumesMixin:

    def _unique_slug(self, con, base, skip_id=None):
        base = base or "resume"
        slug, n = base, 2
        while (slug in RESERVED or con.execute("SELECT 1 FROM resumes WHERE slug = ? AND id != ?",
                                               (slug, skip_id or 0)).fetchone()):
            slug, n = f"{base}-{n}", n + 1
        return slug

    def create_resume(self, user_id, title="My resume", data=None, theme="modern", accent="#4f46e5", lang="en"):
        u = self.user(user_id)
        data = clean_resume_data(data or blank_resume(u["name"], u.get("job_title", ""), u["email"]))
        title = _text(title, 60) or "My resume"
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM resumes WHERE user_id = ?", (user_id,)).fetchone()[0] >= 10:
                raise TaskError("You can have at most 10 resumes.")
            slug = self._unique_slug(con, slugify(data["name"]) or "resume-" + secrets.token_hex(3))
            rid = con.execute("INSERT INTO resumes (user_id, slug, title, theme, accent, lang, data, created_at, updated_at) "
                              "VALUES (?,?,?,?,?,?,?,?,?)",
                              (user_id, slug, title, theme if theme in THEMES else "modern",
                               accent if HEX.match(accent or "") else "#4f46e5", lang, json.dumps(data), now(), now())
                              ).lastrowid
            self.log(con, user_id, "resume_created", title)
            return rid

    def _resume_row(self, r):
        if not r:
            return None
        d = dict(r)
        d["content"] = json.loads(d.pop("data"))
        d["has_photo"] = bool(d.pop("photo", ""))
        return d

    def resume(self, resume_id):
        with self.db() as con:
            return self._resume_row(con.execute("SELECT r.*, u.name AS owner_name FROM resumes r "
                                                "JOIN users u ON u.id = r.user_id WHERE r.id = ?", (resume_id,)).fetchone())

    def resume_by_slug(self, slug):
        with self.db() as con:
            return self._resume_row(con.execute("SELECT r.*, u.name AS owner_name FROM resumes r "
                                                "JOIN users u ON u.id = r.user_id WHERE r.slug = ?",
                                                (str(slug or "").lower(),)).fetchone())

    def resumes_of(self, user_id):
        with self.db() as con:
            return [self._resume_row(r) for r in con.execute(
                "SELECT r.*, u.name AS owner_name FROM resumes r JOIN users u ON u.id = r.user_id "
                "WHERE r.user_id = ? ORDER BY r.updated_at DESC", (user_id,))]

    def public_resumes(self, q="", limit=60):
        sql = ("SELECT r.*, u.name AS owner_name FROM resumes r JOIN users u ON u.id = r.user_id "
               "WHERE r.visibility = 'public' AND u.active = 1")
        args = []
        if q:
            sql += " AND (r.data LIKE ? OR r.title LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY r.updated_at DESC LIMIT ?"
        with self.db() as con:
            return [self._resume_row(r) for r in con.execute(sql, args + [limit])]

    def save_resume(self, user_id, resume_id, payload):
        """Save everything from the editor: settings and content. Returns the (maybe new) slug."""
        r = self.resume(resume_id)
        if not r or r["user_id"] != user_id:
            raise PermissionError
        from world import LANGUAGES
        data = clean_resume_data(payload.get("content"))
        title = _text(payload.get("title"), 60) or r["title"]
        theme = payload.get("theme") if payload.get("theme") in THEMES else r["theme"]
        font = payload.get("font") if payload.get("font") in FONTS else r["font"]
        lang = payload.get("lang") if payload.get("lang") in LANGUAGES else r["lang"]
        vis = payload.get("visibility") if payload.get("visibility") in VISIBILITY else r["visibility"]
        accent = payload.get("accent") if HEX.match(str(payload.get("accent") or "")) else r["accent"]
        slug = str(payload.get("slug") or r["slug"]).strip().lower()
        if not SLUG.match(slug) or slug in RESERVED:
            raise TaskError("The link name may use small letters, digits and dashes (3-40 characters).")
        with self.db() as con:
            if con.execute("SELECT 1 FROM resumes WHERE slug = ? AND id != ?", (slug, resume_id)).fetchone():
                raise TaskError("This link name is already taken. Please choose another one.")
            con.execute("UPDATE resumes SET title = ?, theme = ?, font = ?, lang = ?, visibility = ?, accent = ?, "
                        "slug = ?, data = ?, updated_at = ? WHERE id = ?",
                        (title, theme, font, lang, vis, accent.lower(), slug, json.dumps(data), now(), resume_id))
        return slug

    def set_resume_photo(self, user_id, resume_id, data):
        r = self.resume(resume_id)
        if not r or r["user_id"] != user_id:
            raise PermissionError
        if not data:
            raise TaskError("Please choose a picture.")
        if len(data) > 5 * 1024 * 1024:
            raise TaskError("The picture is too big (max 5 MB).")
        try:
            from PIL import Image, ImageOps
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
            img = img.convert("RGB")
            side = min(img.size)                          # square crop from the middle, 400 x 400
            left, top = (img.width - side) // 2, (img.height - side) // 3
            img = img.crop((left, top, left + side, top + side)).resize((400, 400))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=88)
        except Exception:
            raise TaskError("This file is not a picture (use JPG, PNG or WEBP).")
        with self.db() as con:
            con.execute("UPDATE resumes SET photo = ?, updated_at = ? WHERE id = ?",
                        (base64.b64encode(out.getvalue()).decode(), now(), resume_id))

    def remove_resume_photo(self, user_id, resume_id):
        r = self.resume(resume_id)
        if not r or r["user_id"] != user_id:
            raise PermissionError
        with self.db() as con:
            con.execute("UPDATE resumes SET photo = '' WHERE id = ?", (resume_id,))

    def resume_photo(self, resume_id):
        with self.db() as con:
            r = con.execute("SELECT photo FROM resumes WHERE id = ?", (resume_id,)).fetchone()
        return base64.b64decode(r[0]) if r and r[0] else None

    def duplicate_resume(self, user_id, resume_id):
        r = self.resume(resume_id)
        if not r or r["user_id"] != user_id:
            raise PermissionError
        new = self.create_resume(user_id, f"{r['title']} (copy)"[:60], r["content"], r["theme"], r["accent"], r["lang"])
        photo = self.resume_photo(resume_id)
        with self.db() as con:
            con.execute("UPDATE resumes SET font = ?, photo = ? WHERE id = ?",
                        (r["font"], base64.b64encode(photo).decode() if photo else "", new))
        return new

    def delete_resume(self, user_id, resume_id, is_site_admin=False):
        r = self.resume(resume_id)
        if not r or (r["user_id"] != user_id and not is_site_admin):
            raise PermissionError
        with self.db() as con:
            con.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
            self.log(con, user_id, "resume_deleted", r["title"])

    def count_resume_view(self, resume_id):
        with self.db() as con:
            con.execute("UPDATE resumes SET views = views + 1 WHERE id = ?", (resume_id,))

    def import_resume(self, user_id, text):
        try:
            j = json.loads(text)
        except (ValueError, TypeError):
            raise TaskError("This file is not a JSON file.")
        if isinstance(j, dict) and "basics" in j:                       # JSON Resume format
            return self.create_resume(user_id, "Imported resume", from_json_resume(j))
        if isinstance(j, dict) and isinstance(j.get("content"), dict):   # a file exported from TaskFlow
            return self.create_resume(user_id, j.get("title") or "Imported resume", j["content"],
                                      j.get("theme", "modern"), j.get("accent", "#4f46e5"), j.get("lang", "en"))
        raise TaskError("Unknown resume file. Use a file exported from TaskFlow or a JSON Resume file.")
