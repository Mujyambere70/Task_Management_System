"""
resume_views.py - pages of the resume builder.

    /resumes                 my resumes: make a new one, import, duplicate, delete
    /resumes/<id>/edit       the editor: every change is saved by itself and the preview updates
    /r/<slug>                the resume page to share (print it or save it as PDF in the browser)
    /explore                 public resumes of all people (no account needed)

Resumes belong to a person, not to a workspace, so the same resume is used in every team.
"""

import io
import json
from datetime import datetime

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

import i18n
import world
from core import TaskError
from resumes import FONTS, SECTION_TYPES, THEMES, VISIBILITY

SIDE_TYPES = {"skills", "languages", "interests"}          # shown in the side column of the two-column designs
FONT_CSS = {"inter": "'Inter', system-ui, sans-serif", "serif": "'Merriweather', Georgia, serif",
            "slab": "'Roboto Slab', Georgia, serif"}
EDITOR_STRINGS = [
    "Add entry", "Remove entry", "Move up", "Move down", "Hide section", "Show section", "Remove section",
    "Remove this section?", "Section title", "Text", "Add link", "Label (e.g. GitHub)", "Saving…", "Saved",
    "Not saved:", "Network problem. Please try again.", "Copied!", "Hidden", "Uploading…", "Photo removed.",
    "entries", "Default titles are translated into the language of the resume.",
]


def register(app, store, login_required):

    def tr(text, lang=None):
        return i18n.translate_text(text, lang)

    def own(resume_id):
        r = store.resume(resume_id)
        if not r:
            abort(404)
        if r["user_id"] != g.user["id"]:
            abort(403)
        return r

    def can_view(r):
        if r["visibility"] in ("link", "public"):
            return True
        return bool(g.user and (g.user["id"] == r["user_id"] or g.user["site_admin"]))

    def public_url(r):
        base = app.config.get("APP_URL") or request.host_url.rstrip("/")
        return base.rstrip("/") + url_for("resume_public", slug=r["slug"])

    def sheet_context(r):
        """Everything the resume sheet needs. Default headings are written in the language of the resume."""
        lang, c = r["lang"], r["content"]

        def heading(section):
            default = SECTION_TYPES[section["type"]][0]
            return tr(default, lang) if section["title"] == default else section["title"]

        def when(start, end):
            end = tr("Present", lang) if (end or "").strip().lower() in ("present", "now", "current") else end
            return " – ".join(x for x in (start, end) if x)

        sections = [s for s in c["sections"] if s["visible"] and (s["items"] or s.get("body"))]
        two_cols = r["theme"] in ("modern", "compact")
        side = [s for s in sections if two_cols and s["type"] in SIDE_TYPES]
        main = [s for s in sections if s not in side]
        photo = url_for("resume_photo", slug=r["slug"], v=r["updated_at"][-8:].replace(":", "")) if r["has_photo"] else None
        return dict(r=r, c=c, main=main, side=side, heading=heading, when=when, photo=photo,
                    rt=lambda s: tr(s, lang), font=FONT_CSS.get(r["font"], FONT_CSS["inter"]),
                    rtl=lang in world.RTL_LANGUAGES)

    # ------------------------------------------------------------ my resumes
    @app.route("/resumes")
    @login_required
    def resume_list():
        return render_template("resume/list.html", resumes=store.resumes_of(g.user["id"]), THEMES=THEMES,
                               VISIBILITY=VISIBILITY)

    @app.route("/resumes/new", methods=["POST"])
    @login_required
    def resume_new():
        f = request.form
        try:
            rid = store.create_resume(g.user["id"], f.get("title") or "My resume",
                                      theme=f.get("theme", "modern"), accent=f.get("accent", "#4f46e5"),
                                      lang=f.get("lang") if f.get("lang") in world.LANGUAGES else g.lang)
        except TaskError as e:
            flash(str(e), "error")
            return redirect(url_for("resume_list"))
        return redirect(url_for("resume_edit", resume_id=rid))

    @app.route("/resumes/import", methods=["POST"])
    @login_required
    def resume_import():
        f = request.files.get("file")
        text = f.read().decode("utf-8", "replace") if f and f.filename else request.form.get("text", "")
        try:
            rid = store.import_resume(g.user["id"], text)
            flash("Resume imported. Check it and choose who may see it.", "success")
            return redirect(url_for("resume_edit", resume_id=rid))
        except TaskError as e:
            flash(str(e), "error")
            return redirect(url_for("resume_list"))

    @app.route("/resumes/<int:resume_id>/duplicate", methods=["POST"])
    @login_required
    def resume_duplicate(resume_id):
        own(resume_id)
        try:
            rid = store.duplicate_resume(g.user["id"], resume_id)
            flash("A copy was made. It is private until you share it.", "success")
            return redirect(url_for("resume_edit", resume_id=rid))
        except TaskError as e:
            flash(str(e), "error")
            return redirect(url_for("resume_list"))

    @app.route("/resumes/<int:resume_id>/delete", methods=["POST"])
    @login_required
    def resume_delete(resume_id):
        r = store.resume(resume_id)
        if not r:
            abort(404)
        try:
            store.delete_resume(g.user["id"], resume_id, is_site_admin=g.user["site_admin"])
        except PermissionError:
            abort(403)
        flash("Resume deleted.", "success")
        return redirect(url_for("explore") if r["user_id"] != g.user["id"] else url_for("resume_list"))

    @app.route("/resumes/<int:resume_id>/export.json")
    @login_required
    def resume_export(resume_id):
        r = own(resume_id)
        data = {"app": "TaskFlow resume", "version": 2, "title": r["title"], "theme": r["theme"], "font": r["font"],
                "accent": r["accent"], "lang": r["lang"], "content": r["content"]}
        return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=resume_{r['slug']}.json"})

    # ------------------------------------------------------------ the editor
    @app.route("/resumes/<int:resume_id>/edit")
    @login_required
    def resume_edit(resume_id):
        r = own(resume_id)
        schema = {k: {"title": tr(v[0]), "default": v[0], "fields": [[key, tr(label), kind] for key, label, kind in v[1]]}
                  for k, v in SECTION_TYPES.items()}
        strings = {s: tr(s) for s in EDITOR_STRINGS}
        return render_template("resume/editor.html", r=r, schema=schema, strings=strings, THEMES=THEMES, FONTS=FONTS,
                               VISIBILITY=VISIBILITY, share_url=public_url(r),
                               section_names={k: tr(v[0]) for k, v in SECTION_TYPES.items()})

    @app.route("/resumes/<int:resume_id>/save", methods=["POST"])
    @login_required
    def resume_save(resume_id):
        own(resume_id)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(ok=False, error=tr("The resume data is not valid.")), 400
        try:
            slug = store.save_resume(g.user["id"], resume_id, payload)
        except TaskError as e:
            return jsonify(ok=False, error=tr(str(e))), 400
        r = store.resume(resume_id)
        return jsonify(ok=True, slug=slug, url=public_url(r), saved=datetime.now().strftime("%H:%M:%S"))

    @app.route("/resumes/<int:resume_id>/photo", methods=["POST"])
    @login_required
    def resume_photo_upload(resume_id):
        own(resume_id)
        f = request.files.get("photo")
        try:
            if request.form.get("remove"):
                store.remove_resume_photo(g.user["id"], resume_id)
            else:
                store.set_resume_photo(g.user["id"], resume_id, f.read() if f else b"")
        except TaskError as e:
            if request.headers.get("X-Requested-With") == "fetch":
                return jsonify(ok=False, error=tr(str(e))), 400
            flash(str(e), "error")
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=True)
        return redirect(url_for("resume_edit", resume_id=resume_id))

    # ------------------------------------------------------------ public pages
    @app.route("/r/<slug>")
    def resume_public(slug):
        r = store.resume_by_slug(slug)
        if not r or not can_view(r):
            abort(404)
        mine = bool(g.user and g.user["id"] == r["user_id"])
        embed = request.args.get("embed") == "1" and mine
        if not mine and not request.args.get("embed"):
            store.count_resume_view(r["id"])
        ctx = sheet_context(r)
        return render_template("resume/public.html", mine=mine, embed=embed, share_url=public_url(r),
                               VISIBILITY=VISIBILITY, **ctx)

    @app.route("/r/<slug>/photo.jpg")
    def resume_photo(slug):
        r = store.resume_by_slug(slug)
        if not r or not can_view(r) or not r["has_photo"]:
            abort(404)
        resp = send_file(io.BytesIO(store.resume_photo(r["id"])), mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "public, max-age=86400" if r["visibility"] != "private" else "private, max-age=600"
        return resp

    @app.route("/explore")
    def explore():
        q = " ".join(request.args.get("q", "").split())[:60]
        rows = store.public_resumes(q)
        cards = []
        for r in rows:
            c = r["content"]
            skills = []
            for s in c["sections"]:
                if s["type"] == "skills" and s["visible"]:
                    for it in s["items"]:
                        skills += [x.strip() for x in it.get("items", "").split(",") if x.strip()]
            cards.append({"r": r, "c": c, "skills": skills[:6],
                          "photo": url_for("resume_photo", slug=r["slug"]) if r["has_photo"] else None})
        return render_template("resume/explore.html", cards=cards, q=q)
