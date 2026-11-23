"""
resume_pdf.py - the resume as a real PDF file made on the server (version 3).

Version 2 only had "Print -> Save as PDF" in the browser, so the result changed a little from browser to browser.
This file draws the resume with reportlab: A4, the accent colour of the resume, the photo, clickable links,
and the same sections. The layout follows the design: one column (classic, timeline) or a side column
(modern, compact, elegant).

Fonts: DejaVu Sans (Latin, Cyrillic, Arabic letters); Chinese with the built-in STSong-Light font.
Arabic text is joined and ordered right to left with arabic-reshaper and python-bidi when they are installed.
"""

import io
import os
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Frame, Image, PageTemplate, BaseDocTemplate, Paragraph, Spacer, Table, TableStyle, \
    FrameBreak, KeepTogether

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")]
_fonts_ready = {}


def _fonts():
    """Register the fonts once. Returns (regular, bold) font names for Latin/Cyrillic/Arabic text."""
    if "base" in _fonts_ready:
        return _fonts_ready["base"]
    names = ("Helvetica", "Helvetica-Bold")
    for d in FONT_DIRS:
        reg, bold = os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf")
        if os.path.exists(reg) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("DejaVu", reg))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold))
            names = ("DejaVu", "DejaVu-Bold")
            break
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _fonts_ready["cjk"] = "STSong-Light"
    except Exception:                                        # pragma: no cover
        _fonts_ready["cjk"] = None
    _fonts_ready["base"] = names
    return names


CJK = re.compile(r"[　-鿿가-힯＀-￯]")
ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")


def shape(text):
    """Arabic letters must be joined and written right to left; other text is unchanged."""
    if not ARABIC.search(text or ""):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:                                      # pragma: no cover
        return text


def _font_for(text, bold=False):
    reg, b = _fonts()
    if CJK.search(text or "") and _fonts_ready.get("cjk"):
        return _fonts_ready["cjk"]
    return b if bold else reg


def para(text, style, bold=False, link=None):
    text = shape(str(text or ""))
    font = _font_for(text, bold)
    body = escape(text)
    if link:
        body = f'<link href="{escape(link)}" color="{style.textColor.hexval() if hasattr(style.textColor, "hexval") else "#000"}">{body}</link>'
    return Paragraph(f'<font name="{font}">{body}</font>', style)


def link_text(u):
    return re.sub(r"^(https?://|mailto:)(www\.)?", "", u or "").rstrip("/")


def make_pdf(r, photo_bytes=None, tr=lambda s: s):
    """r: a resume from store.resume() (with r['content']). tr: translates the default headings."""
    from resumes import SECTION_TYPES
    reg, bold = _fonts()
    c = r["content"]
    accent = colors.HexColor(r["accent"] if re.fullmatch(r"#[0-9a-fA-F]{6}", r["accent"] or "") else "#4f46e5")
    ink, soft = colors.HexColor("#1d2433"), colors.HexColor("#5b6475")
    rtl = r["lang"] == "ar"
    align = TA_RIGHT if rtl else TA_LEFT
    theme = r["theme"]
    dark_side = theme == "elegant"
    side_ink = colors.white if dark_side else ink
    S = {
        "name": ParagraphStyle("name", fontName=bold, fontSize=22, leading=26, textColor=ink, alignment=align),
        "headline": ParagraphStyle("hl", fontName=reg, fontSize=11, leading=14, textColor=accent, alignment=align),
        "contact": ParagraphStyle("ct", fontName=reg, fontSize=8.6, leading=11.5, textColor=soft, alignment=align),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=9.6, leading=12, textColor=accent, spaceBefore=8,
                             spaceAfter=3, alignment=align),
        "h2side": ParagraphStyle("h2s", fontName=bold, fontSize=9.6, leading=12,
                                 textColor=colors.white if dark_side else accent, spaceBefore=8, spaceAfter=3, alignment=align),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=10, leading=12.5, textColor=ink, alignment=align),
        "sub": ParagraphStyle("sub", fontName=reg, fontSize=8.6, leading=11, textColor=soft, alignment=align),
        "body": ParagraphStyle("body", fontName=reg, fontSize=9.3, leading=12.3, textColor=ink, alignment=align),
        "side": ParagraphStyle("side", fontName=reg, fontSize=8.8, leading=11.6, textColor=side_ink, alignment=align),
        "sidebold": ParagraphStyle("sideb", fontName=bold, fontSize=9, leading=11.8, textColor=side_ink, alignment=align),
        "bullet": ParagraphStyle("bul", fontName=reg, fontSize=9.1, leading=12, textColor=ink, alignment=align,
                                 leftIndent=0 if rtl else 9, rightIndent=9 if rtl else 0, bulletIndent=0),
    }

    def heading(sec):
        default = SECTION_TYPES[sec["type"]][0]
        return tr(default) if sec["title"] == default else sec["title"]

    def when(a, b):
        b = tr("Present") if (b or "").strip().lower() in ("present", "now", "current") else b
        return " – ".join(x for x in (a, b) if x)

    def bullets(text):
        return [para(line.lstrip("•-–* "), S["bullet"]) for line in (text or "").split("\n") if line.strip()]

    def row(title, date_text, style=S["h3"]):
        if not date_text:
            return para(title, style)
        t = Table([[para(title, style), para(date_text, ParagraphStyle("d", parent=S["sub"], alignment=TA_RIGHT if not rtl else TA_LEFT))]],
                  colWidths=["74%", "26%"])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        if rtl:
            t._cellvalues[0].reverse()
        return t

    def section_flow(sec, in_side=False):
        out = [para(heading(sec).upper(), S["h2side" if in_side else "h2"])]
        st, stb = (S["side"], S["sidebold"]) if in_side else (S["body"], S["h3"])
        kind = sec["type"]
        if kind == "text":
            out += [para(p, st) for p in (sec.get("body") or "").split("\n") if p.strip()]
        elif kind == "skills":
            for it in sec["items"]:
                if it.get("group"):
                    out.append(para(it["group"], stb))
                out.append(para(" · ".join(x.strip() for x in it.get("items", "").split(",") if x.strip()), st))
                out.append(Spacer(1, 3))
        elif kind == "interests":
            out.append(para(" · ".join(it.get("name", "") for it in sec["items"] if it.get("name")), st))
        elif kind == "languages":
            for it in sec["items"]:
                score = int(it.get("score") or 0)
                level = tr(it["level"]) if it.get("level") else ""
                dots = ("●" * score + "○" * (5 - score)) if score else ""
                out.append(para(f"{it.get('name', '')}" + (f" - {level}" if level else "") + (f"  {dots}" if dots else ""), st))
        else:
            for it in sec["items"]:
                block = []
                if kind == "experience":
                    title = " · ".join(x for x in (it.get("role"), it.get("org")) if x)
                    block.append(row(title, when(it.get("start"), it.get("end"))))
                    sub = " · ".join(x for x in (it.get("place"), link_text(it.get("link"))) if x)
                    if sub:
                        block.append(para(sub, S["sub"]))
                elif kind == "education":
                    block.append(row(it.get("degree"), when(it.get("start"), it.get("end"))))
                    sub = " · ".join(x for x in (it.get("school"), it.get("place")) if x)
                    if sub:
                        block.append(para(sub, S["sub"]))
                elif kind == "projects":
                    block.append(row(it.get("name"), when(it.get("start"), it.get("end"))))
                    sub = " · ".join(x for x in (it.get("tech"), link_text(it.get("link"))) if x)
                    if sub:
                        block.append(para(sub, S["sub"], link=it.get("link") or None))
                elif kind == "certifications":
                    block.append(row(it.get("name"), it.get("date")))
                    if it.get("issuer"):
                        block.append(para(it["issuer"], S["sub"]))
                elif kind == "awards":
                    block.append(row(it.get("name"), it.get("date")))
                elif kind == "references":
                    block.append(para(it.get("name"), stb))
                    block.append(para(" · ".join(x for x in (it.get("role"), it.get("contact")) if x), S["sub"]))
                else:
                    block.append(row(it.get("title"), it.get("date")))
                    if it.get("subtitle"):
                        block.append(para(it["subtitle"], S["sub"]))
                block += bullets(it.get("bullets"))
                block.append(Spacer(1, 5))
                out.append(KeepTogether(block))
        out.append(Spacer(1, 4))
        return out

    sections = [s for s in c["sections"] if s["visible"] and (s["items"] or s.get("body"))]
    two_cols = theme in ("modern", "compact", "elegant")
    side = [s for s in sections if two_cols and s["type"] in ("skills", "languages", "interests")]
    main = [s for s in sections if s not in side]

    # ---- header
    contacts = [x for x in (c.get("email"), c.get("phone"), c.get("location"), link_text(c.get("website"))) if x]
    contacts += [(l_.get("label") + ": " if l_.get("label") else "") + link_text(l_.get("url")) for l_ in c.get("links", [])]
    head = [para(c.get("name"), S["name"])]
    if c.get("headline"):
        head.append(para(c["headline"], S["headline"]))
    head.append(Spacer(1, 4))
    head.append(para("   ·   ".join(contacts), S["contact"]))
    if photo_bytes:
        img = Image(io.BytesIO(photo_bytes), width=26 * mm, height=26 * mm)
        cells = [[head, img]] if rtl else [[img, head]]
        header = Table(cells, colWidths=[None, 30 * mm] if rtl else [30 * mm, None])
        header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        head_flow = [header]
    else:
        head_flow = head
    head_flow = head_flow + [Spacer(1, 6)]

    summary = []
    if c.get("summary"):
        summary = [para(tr("Profile").upper(), S["h2"])] + [para(p, S["body"]) for p in c["summary"].split("\n") if p.strip()]
        summary.append(Spacer(1, 4))

    buf = io.BytesIO()
    W, H = A4
    margin = 15 * mm
    head_h = 46 * mm if photo_bytes else 40 * mm
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
                          title=f"{c.get('name')} - {r['title']}", author=c.get("name") or "", creator="TaskFlow")

    def decorate(canvas, _doc):
        canvas.saveState()
        if dark_side and side:
            x = (W - margin - 0.33 * (W - 2 * margin) - 3 * mm) if rtl else 0
            canvas.setFillColor(colors.HexColor("#1f2533"))
            canvas.rect(x, 0, 0.33 * (W - 2 * margin) + margin + 3 * mm, H - head_h - margin + 4 * mm, stroke=0, fill=1)
        canvas.setFillColor(accent)
        if theme in ("modern", "elegant"):
            canvas.rect(0, H - 4 * mm, W, 4 * mm, stroke=0, fill=1)
        elif theme == "compact":
            canvas.rect(0 if not rtl else W - 4 * mm, H - head_h, 4 * mm, head_h, stroke=0, fill=1)
        else:
            canvas.rect(margin, H - head_h + 2 * mm, W - 2 * margin, 0.8 * mm, stroke=0, fill=1)
        canvas.setFont(reg, 7)
        canvas.setFillColor(soft)
        canvas.drawRightString(W - margin, 8 * mm, f"{_doc.page}")
        canvas.restoreState()

    inner_w = W - 2 * margin
    head_frame = Frame(margin, H - margin - head_h + 6 * mm, inner_w, head_h - 6 * mm, id="head", leftPadding=0,
                       rightPadding=0, topPadding=0, bottomPadding=0)
    body_h = H - 2 * margin - head_h + 6 * mm
    if side:
        side_w = 0.33 * inner_w
        gap = 6 * mm
        main_w = inner_w - side_w - gap
        side_x, main_x = (margin + main_w + gap, margin) if rtl else (margin, margin + side_w + gap)
        if theme == "compact":                                   # side column on the right
            side_x, main_x = (margin, margin + side_w + gap) if rtl else (margin + main_w + gap, margin)
        frames = [head_frame,
                  Frame(side_x, margin, side_w, body_h, id="side", leftPadding=3 * mm if dark_side else 0,
                        rightPadding=3 * mm if dark_side else 0, topPadding=2 * mm, bottomPadding=0),
                  Frame(main_x, margin, main_w, body_h, id="main", leftPadding=0, rightPadding=0, topPadding=2 * mm,
                        bottomPadding=0)]
        later = [Frame(main_x, margin, main_w, H - 2 * margin, id="main2", leftPadding=0, rightPadding=0)]
    else:
        frames = [head_frame, Frame(margin, margin, inner_w, body_h, id="main", leftPadding=0, rightPadding=0,
                                    topPadding=2 * mm, bottomPadding=0)]
        later = [Frame(margin, margin, inner_w, H - 2 * margin, id="main2", leftPadding=0, rightPadding=0)]
    doc.addPageTemplates([PageTemplate(id="first", frames=frames, onPage=decorate, autoNextPageTemplate="later"),
                          PageTemplate(id="later", frames=later,
                                       onPage=lambda cv, d: (cv.saveState(), cv.setFont(reg, 7), cv.setFillColor(soft),
                                                             cv.drawRightString(W - margin, 8 * mm, f"{d.page}"),
                                                             cv.restoreState()))])
    story = list(head_flow) + [FrameBreak()]
    if side:
        for sec in side:
            story += section_flow(sec, in_side=True)
        story.append(FrameBreak())
    story += summary
    for sec in main:
        story += section_flow(sec)
    if not sections and not summary:
        story.append(para(tr("This resume is still empty."), S["sub"]))
    doc.build(story)
    return buf.getvalue()
