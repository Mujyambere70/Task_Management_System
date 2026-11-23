"""
planning.py - planning tools of TaskFlow 3 (the "future scope" of version 2).

  * labels (tags) with colours, many per task
  * sprints with a goal, dates, a sprint burndown and a velocity chart
  * dependencies: a task can wait for other tasks; it cannot be finished while they are open
  * recurring tasks: when a repeating task is done, the next one is made with the next due date
  * a Gantt chart: start date -> due date of every task, with arrows for dependencies
  * a suggested estimate from the time spent on similar finished tasks (a small "nearest neighbours" model)
  * one search box for tasks, comments, projects, people and labels
  * a trash: deleted tasks are kept for some days (30 by default) and can be restored
"""

import calendar
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta

from core import TaskError, count_case, now

REPEATS = {"": "Does not repeat", "daily": "Every day", "weekly": "Every week", "biweekly": "Every 2 weeks",
           "monthly": "Every month"}
SPRINT_STATES = {"planned": "Planned", "active": "Active", "closed": "Closed"}
STOP_WORDS = set("the a an and or of to for in on at with by from is are be this that it as new add make update fix "
                 "page task tasks into via".split())


def next_due(day, repeat):
    """The next date of a repeating task (31 January + 1 month = 28 or 29 February)."""
    d = date.fromisoformat(day)
    if repeat == "daily":
        return (d + timedelta(days=1)).isoformat()
    if repeat == "weekly":
        return (d + timedelta(days=7)).isoformat()
    if repeat == "biweekly":
        return (d + timedelta(days=14)).isoformat()
    if repeat == "monthly":
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()
    raise TaskError("Unknown repeat.")


def words(text):
    """The meaningful words of a title: lower case, 3+ letters, without common words. Works for any alphabet."""
    return [w for w in re.findall(r"\w{3,}", (text or "").lower()) if w not in STOP_WORDS and not w.isdigit()]


def parse_day(text, what="Date"):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise TaskError(f"{what} must be a valid date.")


class PlanningMixin:

    # ================================================================ labels
    def labels(self, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT l.*, (SELECT COUNT(*) FROM task_labels x WHERE x.label_id = l.id) AS uses "
                "FROM labels l WHERE l.workspace_id = ? ORDER BY lower(l.name)", (ws,))]

    def save_label(self, user_id, ws, name, color, label_id=None):
        from store import COLORS
        name = " ".join((name or "").split())
        if not 1 <= len(name) <= 24:
            raise TaskError("A label has 1-24 characters.")
        color = color if color in COLORS else COLORS[0]
        with self.db() as con:
            same = con.execute("SELECT id FROM labels WHERE workspace_id = ? AND lower(name) = lower(?)",
                               (ws, name)).fetchone()
            if same and same[0] != label_id:
                raise TaskError("This label already exists.")
            if label_id:
                if not con.execute("UPDATE labels SET name = ?, color = ? WHERE id = ? AND workspace_id = ?",
                                   (name, color, label_id, ws)).rowcount:
                    raise TaskError("Label not found.")
            else:
                label_id = con.execute("INSERT INTO labels (workspace_id, name, color, created_at) VALUES (?,?,?,?)",
                                       (ws, name, color, now())).lastrowid
            self.log(con, user_id, "labels_changed", name, ws=ws)
            return label_id

    def delete_label(self, user_id, ws, label_id):
        with self.db() as con:
            r = con.execute("SELECT name FROM labels WHERE id = ? AND workspace_id = ?", (label_id, ws)).fetchone()
            if not r:
                raise TaskError("Label not found.")
            con.execute("DELETE FROM task_labels WHERE label_id = ?", (label_id,))
            con.execute("DELETE FROM labels WHERE id = ?", (label_id,))
            self.log(con, user_id, "labels_changed", r["name"], ws=ws)

    def task_labels(self, task_ids):
        """{task id: [label, ...]} for many tasks at once (one query)."""
        ids = [int(i) for i in task_ids]
        if not ids:
            return {}
        out = {i: [] for i in ids}
        with self.db() as con:
            for r in con.execute(f"SELECT x.task_id, l.id, l.name, l.color FROM task_labels x JOIN labels l "
                                 f"ON l.id = x.label_id WHERE x.task_id IN ({','.join('?' * len(ids))}) "
                                 f"ORDER BY lower(l.name)", ids):
                out[r[0]].append({"id": r[1], "name": r[2], "color": r[3]})
        return out

    def set_task_labels(self, con, ws, task_id, label_ids):
        ids = {int(i) for i in label_ids or [] if str(i).isdigit()}
        ok = {r[0] for r in con.execute("SELECT id FROM labels WHERE workspace_id = ?", (ws,))}
        con.execute("DELETE FROM task_labels WHERE task_id = ?", (task_id,))
        for lid in sorted(ids & ok):
            con.execute("INSERT INTO task_labels (task_id, label_id) VALUES (?, ?)", (task_id, lid))

    def tasks_with_label(self, ws, label_id):
        with self.db() as con:
            return {r[0] for r in con.execute(
                "SELECT x.task_id FROM task_labels x JOIN labels l ON l.id = x.label_id "
                "WHERE l.id = ? AND l.workspace_id = ?", (label_id, ws))}

    # ================================================================ sprints
    def sprints(self, ws):
        done = count_case("t.status = 'done'")
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                f"SELECT s.*, (SELECT COUNT(*) FROM tasks t WHERE t.sprint_id = s.id AND t.deleted_at IS NULL) AS total, "
                f"(SELECT {done} FROM tasks t WHERE t.sprint_id = s.id AND t.deleted_at IS NULL) AS done "
                f"FROM sprints s WHERE s.workspace_id = ? "
                f"ORDER BY CASE s.status WHEN 'active' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END, s.start_date DESC",
                (ws,))]
        for s in rows:
            s["percent"] = round(100 * s["done"] / s["total"]) if s["total"] else 0
        return rows

    def sprint(self, sprint_id, ws):
        with self.db() as con:
            r = con.execute("SELECT * FROM sprints WHERE id = ? AND workspace_id = ?", (sprint_id, ws)).fetchone()
            return dict(r) if r else None

    def save_sprint(self, user_id, ws, f, sprint_id=None):
        name = " ".join((f.get("name") or "").split())
        if not 2 <= len(name) <= 60:
            raise TaskError("A sprint name has 2-60 characters.")
        start = parse_day(f.get("start_date"), "Start date")
        end = parse_day(f.get("end_date"), "End date")
        if not start or not end:
            raise TaskError("Choose the first and the last day of the sprint.")
        if end < start:
            raise TaskError("The sprint ends before it starts.")
        if (date.fromisoformat(end) - date.fromisoformat(start)).days > 60:
            raise TaskError("A sprint can be at most 60 days long.")
        goal = (f.get("goal") or "").strip()[:300]
        with self.db() as con:
            if sprint_id:
                if not con.execute("UPDATE sprints SET name = ?, goal = ?, start_date = ?, end_date = ? "
                                   "WHERE id = ? AND workspace_id = ?",
                                   (name, goal, start, end, sprint_id, ws)).rowcount:
                    raise TaskError("Sprint not found.")
            else:
                sprint_id = con.execute(
                    "INSERT INTO sprints (workspace_id, name, goal, start_date, end_date, status, created_by, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (ws, name, goal, start, end, "planned", user_id, now())).lastrowid
            self.log(con, user_id, "sprint_saved", name, ws=ws)
            return sprint_id

    def set_sprint_state(self, user_id, ws, sprint_id, state, move_open_to=None):
        """Start a sprint (only one active at a time) or close it; open tasks can move to another sprint."""
        if state not in SPRINT_STATES:
            raise TaskError("Unknown sprint state.")
        with self.db() as con:
            s = con.execute("SELECT * FROM sprints WHERE id = ? AND workspace_id = ?", (sprint_id, ws)).fetchone()
            if not s:
                raise TaskError("Sprint not found.")
            if state == "active" and con.execute("SELECT 1 FROM sprints WHERE workspace_id = ? AND status = 'active' "
                                                 "AND id != ?", (ws, sprint_id)).fetchone():
                raise TaskError("Another sprint is active. Close it first.")
            con.execute("UPDATE sprints SET status = ?, closed_at = ? WHERE id = ?",
                        (state, now() if state == "closed" else None, sprint_id))
            if state == "closed":
                target = int(move_open_to) if str(move_open_to or "").isdigit() else None
                if target and not con.execute("SELECT 1 FROM sprints WHERE id = ? AND workspace_id = ? AND status != "
                                              "'closed'", (target, ws)).fetchone():
                    raise TaskError("Choose a sprint that is not closed.")
                con.execute("UPDATE tasks SET sprint_id = ? WHERE sprint_id = ? AND status != 'done'",
                            (target, sprint_id))
            self.log(con, user_id, "sprint_" + state, s["name"], ws=ws)

    def delete_sprint(self, user_id, ws, sprint_id):
        with self.db() as con:
            s = con.execute("SELECT name FROM sprints WHERE id = ? AND workspace_id = ?", (sprint_id, ws)).fetchone()
            if not s:
                raise TaskError("Sprint not found.")
            con.execute("UPDATE tasks SET sprint_id = NULL WHERE sprint_id = ?", (sprint_id,))
            con.execute("DELETE FROM sprints WHERE id = ?", (sprint_id,))
            self.log(con, user_id, "sprint_deleted", s["name"], ws=ws)

    def sprint_tasks(self, ws, sprint_id):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                self.TASK_SELECT + " WHERE t.workspace_id = ? AND t.sprint_id = ? AND t.deleted_at IS NULL "
                "ORDER BY CASE t.status WHEN 'done' THEN 1 ELSE 0 END, t.due_date", (ws, sprint_id))]

    def sprint_burndown(self, ws, sprint_id, metric="tasks", today=None):
        """remaining(d) = sprint work not finished at the end of day d; ideal line from the scope of day 1 to 0."""
        s = self.sprint(sprint_id, ws)
        if not s:
            raise TaskError("Sprint not found.")
        today = today or date.today()
        start, end = date.fromisoformat(s["start_date"]), date.fromisoformat(s["end_date"])
        rows = self.sprint_tasks(ws, sprint_id)

        def weight(r):
            return 1 if metric == "tasks" else (r["estimate_minutes"] or 0) / 60
        scope = round(sum(weight(r) for r in rows), 1)
        points, d, n = [], start, max(1, (end - start).days)
        while d <= end:
            day_end = d.isoformat() + " 23:59:59"
            done = sum(weight(r) for r in rows if r["completed_at"] and r["completed_at"] <= day_end)
            i = (d - start).days
            points.append({"day": d, "remaining": round(scope - done, 1) if d <= today else None,
                           "ideal": round(scope * (1 - i / n), 1), "scope": scope if d <= today else None})
            d += timedelta(days=1)
        current = next((p["remaining"] for p in reversed(points) if p["remaining"] is not None), scope)
        return {"points": points, "metric": metric, "start": start, "end": end,
                "today": min(max(today, start), end), "remaining": current, "max": max(scope, 1)}

    def velocity(self, ws, limit=8):
        """Work finished inside each sprint (tasks and estimated hours), oldest first, and the average."""
        out = []
        with self.db() as con:
            for s in con.execute("SELECT * FROM sprints WHERE workspace_id = ? AND status != 'planned' "
                                 "ORDER BY start_date DESC LIMIT ?", (ws, limit)).fetchall():
                r = con.execute(
                    f"SELECT {count_case('t.completed_at IS NOT NULL')} AS n, "
                    f"COALESCE(SUM(CASE WHEN t.completed_at IS NOT NULL THEN t.estimate_minutes ELSE 0 END), 0) AS m, "
                    f"COUNT(*) AS planned FROM tasks t WHERE t.sprint_id = ? AND t.deleted_at IS NULL", (s["id"],)).fetchone()
                out.append({"id": s["id"], "name": s["name"], "status": s["status"], "tasks": r["n"],
                            "hours": round((r["m"] or 0) / 60, 1), "planned": r["planned"]})
        out.reverse()
        closed = [v for v in out if v["status"] == "closed"]
        avg = round(sum(v["tasks"] for v in closed) / len(closed), 1) if closed else None
        avg_h = round(sum(v["hours"] for v in closed) / len(closed), 1) if closed else None
        return {"sprints": out, "average": avg, "average_hours": avg_h, "max": max([v["tasks"] for v in out] + [1])}

    # ================================================================ dependencies
    def blockers(self, task_id):
        """Tasks this task waits for."""
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.status, t.due_date FROM task_deps d JOIN tasks t ON t.id = d.blocked_by "
                "WHERE d.task_id = ? AND t.deleted_at IS NULL ORDER BY t.id", (task_id,))]

    def blocking(self, task_id):
        """Tasks that wait for this task."""
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.status FROM task_deps d JOIN tasks t ON t.id = d.task_id "
                "WHERE d.blocked_by = ? AND t.deleted_at IS NULL ORDER BY t.id", (task_id,))]

    def add_dependency(self, user_id, ws, task_id, blocked_by):
        if not str(blocked_by or "").isdigit():
            raise TaskError("Choose the task it waits for.")
        blocked_by = int(blocked_by)
        if blocked_by == task_id:
            raise TaskError("A task cannot wait for itself.")
        with self.db() as con:
            n = con.execute("SELECT COUNT(*) FROM tasks WHERE id IN (?, ?) AND workspace_id = ? AND deleted_at IS NULL",
                            (task_id, blocked_by, ws)).fetchone()[0]
            if n != 2:
                raise TaskError("Task not found.")
            # no circles: the other task must not (directly or indirectly) wait for this one
            edges = {}
            for a, b in con.execute("SELECT d.task_id, d.blocked_by FROM task_deps d JOIN tasks t ON t.id = d.task_id "
                                    "WHERE t.workspace_id = ?", (ws,)):
                edges.setdefault(a, set()).add(b)
            todo, seen = [blocked_by], set()
            while todo:
                x = todo.pop()
                if x == task_id:
                    raise TaskError("This would make a circle: the other task already waits for this one.")
                if x not in seen:
                    seen.add(x)
                    todo.extend(edges.get(x, ()))
            if con.execute("SELECT 1 FROM task_deps WHERE task_id = ? AND blocked_by = ?", (task_id, blocked_by)).fetchone():
                raise TaskError("This link already exists.")
            con.execute("INSERT INTO task_deps (task_id, blocked_by) VALUES (?, ?)", (task_id, blocked_by))
            other = con.execute("SELECT title FROM tasks WHERE id = ?", (blocked_by,)).fetchone()[0]
            self.log(con, user_id, "task_updated", f"waits for “{other}”", task_id, ws=ws)

    def remove_dependency(self, user_id, ws, task_id, blocked_by):
        with self.db() as con:
            if not con.execute("DELETE FROM task_deps WHERE task_id = ? AND blocked_by = ? AND task_id IN "
                               "(SELECT id FROM tasks WHERE workspace_id = ?)", (task_id, blocked_by, ws)).rowcount:
                raise TaskError("Link not found.")
            self.log(con, user_id, "task_updated", "dependency removed", task_id, ws=ws)

    def _check_not_blocked(self, con, task_id):
        r = con.execute("SELECT t.title FROM task_deps d JOIN tasks t ON t.id = d.blocked_by "
                        "WHERE d.task_id = ? AND t.status != 'done' AND t.deleted_at IS NULL ORDER BY t.id",
                        (task_id,)).fetchone()
        if r:
            raise TaskError(f"This task waits for “{r[0]}”. Finish that task first.")

    # ================================================================ recurring tasks
    def _after_done(self, con, user_id, task_id):
        """Called when a task becomes done: a repeating task gets its next copy (once)."""
        t = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not t or not t["repeat"] or not t["due_date"]:
            return None
        nxt = next_due(t["due_date"], t["repeat"])
        start = next_due(t["start_date"], t["repeat"]) if t["start_date"] else None
        con.execute("UPDATE tasks SET repeat = '' WHERE id = ?", (task_id,))      # only the newest copy repeats
        ts = now()
        new = con.execute(
            "INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, created_by, due_date, "
            "created_at, updated_at, workspace_id, estimate_minutes, start_date, sprint_id, repeat, repeat_parent) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t["project_id"], t["title"], t["description"], "todo", t["priority"], t["assignee_id"], t["created_by"],
             nxt, ts, ts, t["workspace_id"], t["estimate_minutes"], start, None, t["repeat"],
             t["repeat_parent"] or t["id"])).lastrowid
        for (lid,) in con.execute("SELECT label_id FROM task_labels WHERE task_id = ?", (task_id,)).fetchall():
            con.execute("INSERT INTO task_labels (task_id, label_id) VALUES (?, ?)", (new, lid))
        for (title,) in con.execute("SELECT title FROM subtasks WHERE task_id = ? ORDER BY id", (task_id,)).fetchall():
            con.execute("INSERT INTO subtasks (task_id, title, done, created_by, created_at) VALUES (?,?,0,?,?)",
                        (new, title, user_id, ts))
        self.log(con, user_id, "task_created", f"next copy, due {nxt}", new, ws=t["workspace_id"], title=t["title"])
        return new

    # ================================================================ Gantt chart
    def gantt(self, ws, project_id=None, days=42, start=None, today=None):
        """Bars from the start date (or the day the task was made) to the due date, with dependency arrows."""
        today = today or date.today()
        start = date.fromisoformat(start) if start else today - timedelta(days=7)
        days = max(14, min(int(days or 42), 120))
        end = start + timedelta(days=days - 1)
        where, args = ["t.workspace_id = ?", "t.deleted_at IS NULL", "t.due_date IS NOT NULL",
                       "(p.archived IS NULL OR p.archived = 0)"], [ws]
        if project_id:
            where.append("t.project_id = ?")
            args.append(project_id)
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                self.TASK_SELECT + f" WHERE {' AND '.join(where)} ORDER BY COALESCE(p.name, 'zz'), "
                "COALESCE(t.start_date, substr(t.created_at, 1, 10)), t.due_date", args)]
            deps = [tuple(r) for r in con.execute(
                "SELECT d.task_id, d.blocked_by FROM task_deps d JOIN tasks t ON t.id = d.task_id "
                "WHERE t.workspace_id = ?", (ws,))]
        bars = []
        for t in rows:
            s = date.fromisoformat(t["start_date"] or t["created_at"][:10])
            e = date.fromisoformat(t["due_date"])
            if s > e:
                s = e
            if e < start or s > end:
                continue
            bars.append({"task": t, "from": max(s, start), "to": min(e, end), "cut_left": s < start,
                         "cut_right": e > end, "late": t["status"] != "done" and e < today})
        rowmap = {b["task"]["id"]: i for i, b in enumerate(bars)}
        arrows = [(rowmap[b], rowmap[a]) for a, b in deps if a in rowmap and b in rowmap]
        return {"start": start, "end": end, "days": days, "today": today, "bars": bars, "arrows": arrows}

    def gantt_svg(self, g, day_w=22, row_h=30, left=230, top=46):
        """Coordinates for the template (the SVG itself is in gantt.html)."""
        out_bars = []
        for i, b in enumerate(g["bars"]):
            x = left + (b["from"] - g["start"]).days * day_w
            w = ((b["to"] - b["from"]).days + 1) * day_w
            out_bars.append(dict(b, x=x, w=w, y=top + i * row_h, row=i))
        arrows = []
        for to_i, from_i in g["arrows"]:
            a, b = out_bars[from_i], out_bars[to_i]
            x1, y1 = a["x"] + a["w"], a["y"] + row_h / 2 - 4
            x2, y2 = b["x"], b["y"] + row_h / 2 - 4
            mid = max(x1 + 8, x2 - 8)
            arrows.append(f"M{x1},{y1} H{mid} V{y2} H{x2}")
        days = []
        for i in range(g["days"]):
            d = g["start"] + timedelta(days=i)
            days.append({"x": left + i * day_w, "day": d, "weekend": d.weekday() >= 5, "first": d.day == 1 or i == 0})
        today_x = left + (g["today"] - g["start"]).days * day_w + day_w / 2 if g["start"] <= g["today"] <= g["end"] else None
        return {"bars": out_bars, "arrows": arrows, "days": days, "today_x": today_x, "left": left, "top": top,
                "row_h": row_h, "day_w": day_w, "width": left + g["days"] * day_w + 10,
                "height": top + max(1, len(out_bars)) * row_h + 10}

    # ================================================================ suggested estimate
    def suggest_estimate(self, ws, title, project_id=None, exclude=None, k=5):
        """Nearest neighbours: finished tasks whose titles share the most words with this title (cosine similarity
        of word counts, +0.1 when in the same project). The suggestion is the similarity-weighted mean of the time
        really logged on the closest ones (their estimate when no time was logged)."""
        mine = Counter(words(title))
        if not mine:
            return None
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.project_id, t.estimate_minutes, "
                "(SELECT COALESCE(SUM(minutes), 0) FROM time_entries e WHERE e.task_id = t.id) AS logged "
                "FROM tasks t WHERE t.workspace_id = ? AND t.status = 'done' AND t.deleted_at IS NULL", (ws,))]
        norm_a = math.sqrt(sum(v * v for v in mine.values()))
        scored = []
        for r in rows:
            if r["id"] == exclude:
                continue
            minutes = r["logged"] or r["estimate_minutes"]
            if not minutes:
                continue
            other = Counter(words(r["title"]))
            if not other:
                continue
            dot = sum(mine[w] * other[w] for w in mine)
            if not dot:
                continue
            sim = dot / (norm_a * math.sqrt(sum(v * v for v in other.values())))
            if project_id and r["project_id"] == project_id:
                sim += 0.1
            scored.append((sim, minutes, r))
        scored.sort(key=lambda x: (-x[0], x[2]["id"]))
        best = [s for s in scored[:k] if s[0] >= 0.2]
        if not best:
            return None
        weight = sum(s for s, _, _ in best)
        minutes = sum(s * m for s, m, _ in best) / weight
        minutes = max(5, int(round(minutes / 5.0)) * 5)                    # to 5 minutes
        return {"minutes": minutes, "count": len(best),
                "examples": [{"id": r["id"], "title": r["title"], "minutes": m, "similarity": round(min(s, 1), 2)}
                             for s, m, r in best]}

    # ================================================================ search
    def search(self, ws, q, limit=20):
        q = " ".join((q or "").split())[:80]
        if len(q) < 2:
            return None
        like = f"%{q.lower()}%"
        with self.db() as con:
            tasks = [dict(r) for r in con.execute(
                self.TASK_SELECT + " WHERE t.workspace_id = ? AND t.deleted_at IS NULL AND "
                "(lower(t.title) LIKE ? OR lower(t.description) LIKE ?) ORDER BY t.updated_at DESC LIMIT ?",
                (ws, like, like, limit))]
            comments = [dict(r) for r in con.execute(
                "SELECT c.id, c.body, c.created_at, c.task_id, t.title, u.name AS user_name FROM comments c "
                "JOIN tasks t ON t.id = c.task_id LEFT JOIN users u ON u.id = c.user_id "
                "WHERE t.workspace_id = ? AND t.deleted_at IS NULL AND lower(c.body) LIKE ? "
                "ORDER BY c.id DESC LIMIT ?", (ws, like, limit))]
            projects = [dict(r) for r in con.execute(
                "SELECT * FROM projects WHERE workspace_id = ? AND (lower(name) LIKE ? OR lower(description) LIKE ?) "
                "ORDER BY lower(name) LIMIT ?", (ws, like, like, limit))]
            people = [dict(r) for r in con.execute(
                "SELECT u.id, u.name, u.email, u.job_title, u.color FROM users u JOIN memberships m ON m.user_id = u.id "
                "WHERE m.workspace_id = ? AND (lower(u.name) LIKE ? OR lower(u.email) LIKE ? OR lower(u.job_title) LIKE ?) "
                "ORDER BY lower(u.name) LIMIT ?", (ws, like, like, like, limit))]
            labels = [dict(r) for r in con.execute(
                "SELECT * FROM labels WHERE workspace_id = ? AND lower(name) LIKE ? ORDER BY lower(name) LIMIT ?",
                (ws, like, limit))]
        return {"q": q, "tasks": tasks, "comments": comments, "projects": projects, "people": people, "labels": labels,
                "total": len(tasks) + len(comments) + len(projects) + len(people) + len(labels)}

    # ================================================================ trash
    def _to_trash(self, con, user_id, ws, task_id):
        """Move a task to the trash. It keeps everything, but its workspace number becomes negative (-ws), so every
        page, report and reminder of the workspace stops seeing it at once. Restoring makes the number positive."""
        con.execute("UPDATE tasks SET deleted_at = ?, deleted_by = ?, workspace_id = ? WHERE id = ?",
                    (now(), user_id, -ws, task_id))
        for table in ("attachments", "time_entries"):
            con.execute(f"UPDATE {table} SET workspace_id = ? WHERE task_id = ?", (-ws, task_id))

    def trash(self, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.status, t.deleted_at, t.created_by, u.name AS deleted_by_name, "
                "p.name AS project_name FROM tasks t LEFT JOIN users u ON u.id = t.deleted_by "
                "LEFT JOIN projects p ON p.id = t.project_id WHERE t.workspace_id = ? AND t.deleted_at IS NOT NULL "
                "ORDER BY t.deleted_at DESC", (-ws,))]

    def restore_task(self, user, ws, task_id):
        with self.db() as con:
            t = con.execute("SELECT title, created_by FROM tasks WHERE id = ? AND workspace_id = ? "
                            "AND deleted_at IS NOT NULL", (task_id, -ws)).fetchone()
            if not t:
                raise TaskError("This task is not in the trash.")
            if user["role"] != "admin" and user["id"] != t["created_by"]:
                raise TaskError("Only the person who made the task or an admin can restore it.")
            con.execute("UPDATE tasks SET deleted_at = NULL, deleted_by = NULL, updated_at = ?, workspace_id = ? "
                        "WHERE id = ?", (now(), ws, task_id))
            for table in ("attachments", "time_entries"):
                con.execute(f"UPDATE {table} SET workspace_id = ? WHERE task_id = ?", (ws, task_id))
            self.log(con, user["id"], "task_restored", t["title"], task_id, ws=ws, title=t["title"])

    def purge_task(self, user, ws, task_id):
        with self.db() as con:
            t = con.execute("SELECT title FROM tasks WHERE id = ? AND workspace_id = ? AND deleted_at IS NOT NULL",
                            (task_id, -ws)).fetchone()
            if not t:
                raise TaskError("This task is not in the trash.")
            if user["role"] != "admin":
                raise TaskError("Only an admin can delete a task for good.")
            self._purge(con, [task_id])
            self.log(con, user["id"], "task_purged", t["title"], ws=ws)

    def _purge(self, con, ids):
        for tid in ids:
            con.execute("DELETE FROM task_labels WHERE task_id = ?", (tid,))
            con.execute("DELETE FROM task_deps WHERE task_id = ? OR blocked_by = ?", (tid, tid))
            con.execute("DELETE FROM tasks WHERE id = ?", (tid,))

    def empty_old_trash(self, days=None):
        days = days if days is not None else int(self.settings().get("trash_days") or 30)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM tasks WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                                             (cutoff,))]
            self._purge(con, ids)
        return len(ids)

    # ================================================================ export / import of a workspace
    def export_workspace(self, ws):
        """Everything of one workspace as JSON (files without their content)."""
        w = self.workspace(ws)
        with self.db() as con:
            def rows(sql, *a):
                return [dict(r) for r in con.execute(sql, a)]
            data = {
                "app": "TaskFlow", "version": 3, "kind": "workspace", "exported_at": now(),
                "workspace": {k: w[k] for k in ("name", "description", "color", "default_priority")},
                "members": rows("SELECT u.name, u.email, m.role FROM memberships m JOIN users u ON u.id = m.user_id "
                                "WHERE m.workspace_id = ? ORDER BY u.id", ws),
                "projects": rows("SELECT id, name, description, color, archived FROM projects WHERE workspace_id = ?", ws),
                "labels": rows("SELECT id, name, color FROM labels WHERE workspace_id = ?", ws),
                "sprints": rows("SELECT id, name, goal, start_date, end_date, status FROM sprints WHERE workspace_id = ?", ws),
                "tasks": rows("SELECT t.id, t.title, t.description, t.status, t.priority, t.due_date, t.start_date, "
                              "t.estimate_minutes, t.project_id, t.sprint_id, t.repeat, t.created_at, t.completed_at, "
                              "u.email AS assignee FROM tasks t LEFT JOIN users u ON u.id = t.assignee_id "
                              "WHERE t.workspace_id = ? AND t.deleted_at IS NULL ORDER BY t.id", ws),
                "task_labels": rows("SELECT x.task_id, x.label_id FROM task_labels x JOIN tasks t ON t.id = x.task_id "
                                    "WHERE t.workspace_id = ?", ws),
                "dependencies": rows("SELECT d.task_id, d.blocked_by FROM task_deps d JOIN tasks t ON t.id = d.task_id "
                                     "WHERE t.workspace_id = ?", ws),
                "checklists": rows("SELECT s.task_id, s.title, s.done FROM subtasks s JOIN tasks t ON t.id = s.task_id "
                                   "WHERE t.workspace_id = ?", ws),
                "comments": rows("SELECT c.task_id, u.email AS author, c.body, c.created_at FROM comments c JOIN tasks t "
                                 "ON t.id = c.task_id LEFT JOIN users u ON u.id = c.user_id WHERE t.workspace_id = ?", ws),
                "time": rows("SELECT e.task_id, u.email AS person, e.started_at, e.minutes, e.note FROM time_entries e "
                             "JOIN users u ON u.id = e.user_id WHERE e.workspace_id = ?", ws),
                "files": rows("SELECT task_id, name, mime, size, created_at FROM attachments WHERE workspace_id = ?", ws),
            }
        return json.dumps(data, ensure_ascii=False, indent=1, default=str)

    def import_tasks_csv(self, user_id, ws, text):
        """Tasks from a CSV file with a header: title (needed), description, status, priority, due date,
        assignee email, project, estimate, labels (separated by ;). The TaskFlow tasks CSV works too."""
        import csv
        import io
        from store import PRIORITY_NAMES, STATUS_NAMES
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        if not reader.fieldnames:
            raise TaskError("The file is empty.")
        names = {(h or "").strip().lower(): h for h in reader.fieldnames}

        def col(row, *keys):
            for k in keys:
                if k in names and row.get(names[k]) not in (None, ""):
                    return str(row[names[k]]).strip()
            return ""
        if "title" not in names:
            raise TaskError("The first line must name the columns, with at least “title”.")
        status_by_name = {v.lower(): k for k, v in STATUS_NAMES.items()} | {k: k for k in STATUS_NAMES}
        prio_by_name = {v.lower(): k for k, v in PRIORITY_NAMES.items()} | {k: k for k in PRIORITY_NAMES}
        people = {u["email"]: u["id"] for u in self.users(ws, active_only=True)}
        projects = {p["name"].lower(): p["id"] for p in self.projects(ws, include_archived=True)}
        labels = {lb["name"].lower(): lb["id"] for lb in self.labels(ws)}
        made, errors = 0, []
        for n, row in enumerate(reader, start=2):
            if n > 501:
                errors.append("Only the first 500 tasks were read.")
                break
            try:
                pname = col(row, "project").lower()
                if pname and pname not in projects:
                    projects[pname] = self.save_project(user_id, ws, col(row, "project"), "", None)
                fields = {"title": col(row, "title"), "description": col(row, "description"),
                          "status": status_by_name.get(col(row, "status").lower(), "todo"),
                          "priority": prio_by_name.get(col(row, "priority").lower(), "medium"),
                          "assignee_id": people.get(col(row, "assignee email", "assignee", "assigned to").lower()),
                          "project_id": projects.get(pname), "estimate": col(row, "estimate", "estimate (h)"),
                          "_import": True}
                due = col(row, "due date", "due_date", "due")
                if due:
                    fields["due_date"] = due
                tid = self.create_task(user_id, ws, fields)
                wanted = [x.strip().lower() for x in col(row, "labels").split(";") if x.strip()]
                if wanted:
                    with self.db() as con:
                        for name in wanted:
                            if name not in labels:
                                labels[name] = con.execute(
                                    "INSERT INTO labels (workspace_id, name, color, created_at) VALUES (?,?,?,?)",
                                    (ws, name[:24], "#0ea5e9", now())).lastrowid
                        self.set_task_labels(con, ws, tid, [labels[x] for x in wanted])
                made += 1
            except TaskError as e:
                errors.append(f"Line {n}: {e}")
        return made, errors[:20]
