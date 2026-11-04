"""
files.py - files attached to tasks.

Files are kept inside the database (as base64 text), not on the server's disk: free cloud
servers delete their disk at every restart, the database keeps everything.
"""

import base64
import mimetypes
import re

from core import TaskError, now

MAX_FILE_BYTES = 5 * 1024 * 1024          # 5 MB per file
MAX_FILES_PER_TASK = 10
ALLOWED = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "csv", "md", "json", "docx", "xlsx", "pptx",
           "odt", "ods", "zip", "py", "r", "ipynb", "sql"}
IMAGES = {"png", "jpg", "jpeg", "gif", "webp"}
SIGNATURES = {"png": b"\x89PNG", "jpg": b"\xff\xd8", "jpeg": b"\xff\xd8", "gif": b"GIF8", "pdf": b"%PDF",
              "zip": b"PK", "docx": b"PK", "xlsx": b"PK", "pptx": b"PK", "odt": b"PK", "ods": b"PK"}


def clean_filename(name):
    """Keep letters of every language, digits, dot, dash, underscore and spaces; nothing that looks like a path."""
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\- ()]", "_", name, flags=re.UNICODE).strip(" .")
    return name[:120] or "file"


def extension(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


class FilesMixin:

    def add_attachment(self, user_id, task_id, filename, data):
        name = clean_filename(filename)
        ext = extension(name)
        if ext not in ALLOWED:
            raise TaskError("This type of file is not allowed. Use PDF, pictures, Office files, text, CSV or ZIP.")
        if not data:
            raise TaskError("The file is empty.")
        if len(data) > MAX_FILE_BYTES:
            raise TaskError("The file is too big (max 5 MB).")
        sig = SIGNATURES.get(ext)
        if ext == "webp":
            ok = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        else:
            ok = sig is None or data.startswith(sig)
        if not ok:
            raise TaskError("The file does not match its type.")
        mime = mimetypes.types_map.get("." + ext) or {
            "md": "text/markdown", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "webp": "image/webp", "ipynb": "application/json", "r": "text/plain", "sql": "text/plain",
        }.get(ext, "application/octet-stream")
        with self.db() as con:
            t = con.execute("SELECT workspace_id, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not t:
                raise TaskError("Task not found.")
            if con.execute("SELECT COUNT(*) FROM attachments WHERE task_id = ?", (task_id,)).fetchone()[0] \
                    >= MAX_FILES_PER_TASK:
                raise TaskError("A task can have at most 10 files.")
            aid = con.execute("INSERT INTO attachments (task_id, workspace_id, user_id, name, mime, size, data, "
                              "created_at) VALUES (?,?,?,?,?,?,?,?)",
                              (task_id, t["workspace_id"], user_id, name, mime, len(data),
                               base64.b64encode(data).decode(), now())).lastrowid
            con.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now(), task_id))
            self.log(con, user_id, "attachment_added", name, task_id, ws=t["workspace_id"], title=t["title"])
            return aid

    def attachments(self, task_id):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT a.id, a.task_id, a.workspace_id, a.user_id, a.name, a.mime, a.size, a.created_at, "
                "u.name AS user_name FROM attachments a LEFT JOIN users u ON u.id = a.user_id "
                "WHERE a.task_id = ? ORDER BY a.id", (task_id,))]

    def attachment(self, attachment_id, with_data=False):
        cols = "a.*" if with_data else "a.id, a.task_id, a.workspace_id, a.user_id, a.name, a.mime, a.size, a.created_at"
        with self.db() as con:
            r = con.execute(f"SELECT {cols} FROM attachments a WHERE a.id = ?", (attachment_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        if with_data:
            d["bytes"] = base64.b64decode(d.pop("data"))
        d["is_image"] = extension(d["name"]) in IMAGES
        return d

    def delete_attachment(self, user_id, attachment_id):
        with self.db() as con:
            a = con.execute("SELECT a.task_id, a.workspace_id, a.name, t.title FROM attachments a "
                            "JOIN tasks t ON t.id = a.task_id WHERE a.id = ?", (attachment_id,)).fetchone()
            if not a:
                raise TaskError("File not found.")
            con.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
            self.log(con, user_id, "attachment_deleted", a["name"], a["task_id"], ws=a["workspace_id"],
                     title=a["title"])
            return a["task_id"]
