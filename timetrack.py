"""
timetrack.py - time tracking (a start/stop timer or time typed by hand), estimates and burndown charts.

A person can have one running timer. Starting a timer on another task stops the first one.
The burndown chart shows, for every day of a period, how much work was still open
(number of tasks, or estimated hours) next to the ideal straight line down to zero.
"""

import math
from datetime import date, datetime, timedelta

from core import TaskError, count_case, now

MAX_TIMER_MINUTES = 12 * 60          # a forgotten timer counts at most 12 hours


def _minutes_between(start, end):
    a = datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
    b = datetime.strptime(end[:19], "%Y-%m-%d %H:%M:%S")
    return max(1, min(MAX_TIMER_MINUTES, math.ceil((b - a).total_seconds() / 60)))


class TimeMixin:

    def running_timer(self, user_id):
        with self.db() as con:
            r = con.execute("SELECT e.*, t.title FROM time_entries e JOIN tasks t ON t.id = e.task_id "
                            "WHERE e.user_id = ? AND e.ended_at IS NULL ORDER BY e.id DESC", (user_id,)).fetchone()
            return dict(r) if r else None

    def start_timer(self, user_id, task_id):
        stopped = self.stop_timer(user_id)
        with self.db() as con:
            t = con.execute("SELECT workspace_id, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not t:
                raise TaskError("Task not found.")
            con.execute("INSERT INTO time_entries (task_id, workspace_id, user_id, started_at, created_at) "
                        "VALUES (?,?,?,?,?)", (task_id, t["workspace_id"], user_id, now(), now()))
        return stopped

    def stop_timer(self, user_id, at=None):
        """Stop the running timer (if any). Returns the minutes counted, or None."""
        with self.db() as con:
            r = con.execute("SELECT e.*, t.title FROM time_entries e JOIN tasks t ON t.id = e.task_id "
                            "WHERE e.user_id = ? AND e.ended_at IS NULL", (user_id,)).fetchall()
            total = None
            for e in r:
                end = at or now()
                minutes = _minutes_between(e["started_at"], end)
                # only the request that really stops it counts the time (two clicks at once count once)
                if con.execute("UPDATE time_entries SET ended_at = ?, minutes = ? WHERE id = ? AND ended_at IS NULL",
                               (end, minutes, e["id"])).rowcount:
                    total = (total or 0) + minutes
                    self.log(con, user_id, "time_logged", f"{minutes} min (timer)", e["task_id"],
                             ws=e["workspace_id"], title=e["title"])
            return total

    def log_time(self, user_id, task_id, minutes, day=None, note=""):
        if not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60:
            raise TaskError("Time for one entry must be between 1 minute and 24 hours.")
        try:
            day = date.fromisoformat(day) if day else date.today()
        except ValueError:
            raise TaskError("Please choose a valid day.")
        if day > date.today():
            raise TaskError("You cannot log time for a future day.")
        note = " ".join((note or "").split())[:200]
        start = datetime.combine(day, datetime.min.time()).replace(hour=9)
        with self.db() as con:
            t = con.execute("SELECT workspace_id, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not t:
                raise TaskError("Task not found.")
            con.execute("INSERT INTO time_entries (task_id, workspace_id, user_id, started_at, ended_at, minutes, note, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (task_id, t["workspace_id"], user_id, start.strftime("%Y-%m-%d %H:%M:%S"),
                         (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"), minutes, note, now()))
            self.log(con, user_id, "time_logged", f"{minutes} min", task_id, ws=t["workspace_id"], title=t["title"])

    def time_entries(self, task_id):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT e.*, u.name, u.color FROM time_entries e JOIN users u ON u.id = e.user_id "
                "WHERE e.task_id = ? ORDER BY e.started_at DESC, e.id DESC", (task_id,))]

    def time_entry(self, entry_id):
        with self.db() as con:
            r = con.execute("SELECT * FROM time_entries WHERE id = ?", (entry_id,)).fetchone()
            return dict(r) if r else None

    def delete_time_entry(self, user, entry_id):
        e = self.time_entry(entry_id)
        if not e:
            raise TaskError("Time entry not found.")
        if user["role"] != "admin" and user["id"] != e["user_id"]:
            raise PermissionError
        with self.db() as con:
            con.execute("DELETE FROM time_entries WHERE id = ?", (entry_id,))
        return e["task_id"]

    def time_report(self, ws, days=30, today=None):
        """Logged hours per person and per project in the last `days` days, and the total."""
        today = today or date.today()
        since = (today - timedelta(days=days - 1)).isoformat()
        with self.db() as con:
            people = [dict(r) for r in con.execute(
                "SELECT u.id, u.name, u.color, COALESCE(SUM(e.minutes), 0) AS minutes, COUNT(DISTINCT e.task_id) AS tasks "
                "FROM time_entries e JOIN users u ON u.id = e.user_id WHERE e.workspace_id = ? AND e.started_at >= ? "
                "AND e.ended_at IS NOT NULL GROUP BY u.id, u.name, u.color ORDER BY COALESCE(SUM(e.minutes), 0) DESC",
                (ws, since))]
            projects = [dict(r) for r in con.execute(
                "SELECT COALESCE(p.name, '') AS name, COALESCE(p.color, '#94a3b8') AS color, "
                "COALESCE(SUM(e.minutes), 0) AS minutes FROM time_entries e JOIN tasks t ON t.id = e.task_id "
                "LEFT JOIN projects p ON p.id = t.project_id WHERE e.workspace_id = ? AND e.started_at >= ? "
                "AND e.ended_at IS NOT NULL GROUP BY p.name, p.color ORDER BY COALESCE(SUM(e.minutes), 0) DESC",
                (ws, since))]
            est = con.execute(
                "SELECT COALESCE(SUM(estimate_minutes), 0) AS est, "
                "COALESCE(SUM((SELECT COALESCE(SUM(minutes), 0) FROM time_entries x WHERE x.task_id = t.id)), 0) AS logged "
                "FROM tasks t WHERE t.workspace_id = ? AND t.estimate_minutes IS NOT NULL", (ws,)).fetchone()
        total = sum(p["minutes"] for p in people)
        for row in people + projects:
            row["share"] = round(row["minutes"] / total * 100) if total else 0
        return {"people": people, "projects": projects, "total": total, "days": days,
                "estimated": est["est"], "logged_on_estimated": est["logged"]}

    def time_entries_csv(self, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT e.started_at, e.ended_at, e.minutes, e.note, u.name AS person, t.title AS task, "
                "COALESCE(p.name, '') AS project FROM time_entries e JOIN users u ON u.id = e.user_id "
                "JOIN tasks t ON t.id = e.task_id LEFT JOIN projects p ON p.id = t.project_id "
                "WHERE e.workspace_id = ? AND e.ended_at IS NOT NULL ORDER BY e.started_at DESC", (ws,))]

    # ------------------------------------------------------------------ burndown
    def burndown(self, ws, project_id=None, days=14, metric="tasks", today=None):
        """One point per day for the last `days` days (and the days until the latest due date of the open work).

        remaining(d) = work created on or before day d and not finished by the end of day d.
        metric 'tasks' counts tasks; 'hours' sums the estimates (tasks without an estimate count 0).
        """
        today = today or date.today()
        days = max(7, min(int(days or 14), 90))
        start = today - timedelta(days=days - 1)
        where, args = ["workspace_id = ?"], [ws]
        if project_id:
            where.append("project_id = ?")
            args.append(project_id)
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                f"SELECT created_at, completed_at, due_date, estimate_minutes, status FROM tasks "
                f"WHERE {' AND '.join(where)}", args)]
        open_due = [r["due_date"] for r in rows if r["status"] != "done" and r["due_date"] and r["due_date"] > today.isoformat()]
        end = min(max([today] + [date.fromisoformat(d) for d in open_due]), today + timedelta(days=30))

        def weight(r):
            return 1 if metric == "tasks" else (r["estimate_minutes"] or 0) / 60

        points = []
        d = start
        while d <= end:
            day_end = d.isoformat() + " 23:59:59"
            scope = sum(weight(r) for r in rows if r["created_at"] <= day_end)
            done = sum(weight(r) for r in rows if r["completed_at"] and r["completed_at"] <= day_end)
            points.append({"day": d, "remaining": round(scope - done, 1) if d <= today else None,
                           "scope": round(scope, 1) if d <= today else None})
            d += timedelta(days=1)
        first = points[0]["remaining"] or 0
        n = len(points) - 1
        for i, p in enumerate(points):
            p["ideal"] = round(first * (1 - i / n), 1) if n else first
        current = next(p["remaining"] for p in reversed(points) if p["remaining"] is not None)
        return {"points": points, "metric": metric, "start": start, "end": end, "today": today,
                "remaining": current, "max": max([p["scope"] or 0 for p in points] + [first, 1])}

    def chart_svg_points(self, bd, width=640, height=220, pad=34):
        """Turn burndown points into SVG coordinates (kept here so the template stays simple)."""
        pts, top = bd["points"], bd["max"] * 1.1
        n = max(1, len(pts) - 1)

        def xy(i, v):
            return round(pad + i * (width - pad - 10) / n, 1), round(height - pad - (v / top) * (height - pad - 10), 1)

        def line(key):
            return " ".join("%s,%s" % xy(i, p[key]) for i, p in enumerate(pts) if p[key] is not None)
        today_i = next((i for i, p in enumerate(pts) if p["day"] == bd["today"]), len(pts) - 1)
        ticks = [(round(height - pad - (v / top) * (height - pad - 10), 1), v)
                 for v in sorted({0, round(top / 2), round(bd["max"])})]
        labels = [(xy(i, 0)[0], p["day"]) for i, p in enumerate(pts) if i % max(1, len(pts) // 7) == 0]
        return {"remaining": line("remaining"), "ideal": line("ideal"), "scope": line("scope"),
                "today_x": xy(today_i, 0)[0], "ticks": ticks, "labels": labels, "w": width, "h": height, "pad": pad}

    def workload(self, ws):
        """Open tasks and open estimated hours per member - helps to share the work fairly."""
        with self.db() as con:
            return [dict(r) for r in con.execute(
                f"SELECT u.id, u.name, u.color, {count_case('t.id IS NOT NULL')} AS open_tasks, "
                f"COALESCE(SUM(t.estimate_minutes), 0) AS open_minutes FROM memberships m JOIN users u ON u.id = m.user_id "
                f"LEFT JOIN tasks t ON t.assignee_id = u.id AND t.workspace_id = m.workspace_id AND t.status != 'done' "
                f"WHERE m.workspace_id = ? GROUP BY u.id, u.name, u.color ORDER BY lower(u.name)", (ws,))]
