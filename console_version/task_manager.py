"""
Task Manager - a small command-line program that helps a team manage tasks.

Users log in, add tasks for team members, view and edit their own tasks,
and the admin can register users and generate reports.

Data is kept in two plain text files next to the program:
    user.txt   username;password_hash
    tasks.txt  username;title;description;due_date;assigned_date;completed

Run:  python task_manager.py
First login (if user.txt does not exist yet):  admin / password
"""

import getpass
import hashlib
import hmac
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import date, datetime

DATE_FORMAT = "%Y-%m-%d"
USER_FILE = "user.txt"
TASK_FILE = "tasks.txt"
TASK_REPORT = "task_overview.txt"
USER_REPORT = "user_overview.txt"
ADMIN = "admin"
DEFAULT_ADMIN_PASSWORD = "password"
MIN_PASSWORD_LENGTH = 6


class TaskManagerError(Exception):
    """A problem the user can fix (wrong input, unknown user, ...)."""


# ---------------------------------------------------------------- passwords

def hash_password(password, salt=None):
    """Return 'pbkdf2$salt$hash' - the password itself is never saved."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return f"pbkdf2${salt}${digest.hex()}"


def check_password(password, stored):
    if stored.startswith("pbkdf2$"):
        _, salt, _ = stored.split("$")
        return hmac.compare_digest(hash_password(password, salt), stored)
    # old user.txt files kept plain passwords - still accepted, upgraded at login
    return hmac.compare_digest(password, stored)


# ---------------------------------------------------------------- helpers

def parse_date(text):
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT).date()
    except ValueError:
        raise TaskManagerError("Invalid date. Please use YYYY-MM-DD, for example 2026-12-31.")


def clean(text, field, allow_empty=False):
    """Text is saved with ';' between fields, so ';' and new lines are not allowed."""
    text = text.strip()
    if not text and not allow_empty:
        raise TaskManagerError(f"{field} cannot be empty.")
    if ";" in text or "\n" in text:
        raise TaskManagerError(f"{field} cannot contain ';'.")
    return text


def percent(part, whole):
    return f"{part / whole * 100:.2f}%" if whole else "0.00%"


@dataclass
class Task:
    username: str
    title: str
    description: str
    due_date: date
    assigned_date: date
    completed: bool = False

    def is_overdue(self, today=None):
        return not self.completed and self.due_date < (today or date.today())

    def to_line(self):
        return ";".join([self.username, self.title, self.description,
                         self.due_date.strftime(DATE_FORMAT),
                         self.assigned_date.strftime(DATE_FORMAT),
                         "Yes" if self.completed else "No"])

    @classmethod
    def from_line(cls, line):
        parts = line.split(";")
        if len(parts) != 6:
            raise ValueError(f"bad task line: {line!r}")
        username, title, description, due, assigned, completed = parts
        return cls(username, title, description, parse_date(due), parse_date(assigned),
                   completed.strip() == "Yes")

    def details(self, number=None):
        head = f"Task {number}: " if number else "Task: "
        return (f"{head}{self.title}\n"
                f"  Assigned to:   {self.username}\n"
                f"  Date assigned: {self.assigned_date.strftime(DATE_FORMAT)}\n"
                f"  Due date:      {self.due_date.strftime(DATE_FORMAT)}"
                f"{'  (OVERDUE)' if self.is_overdue() else ''}\n"
                f"  Completed:     {'Yes' if self.completed else 'No'}\n"
                f"  Description:   {self.description}\n")


# ---------------------------------------------------------------- the manager

class TaskManager:
    """All the logic and file storage. The menu code below only asks questions."""

    def __init__(self, folder="."):
        self.folder = folder
        self.users = {}     # username -> password hash
        self.tasks = []
        self.load()

    def path(self, name):
        return os.path.join(self.folder, name)

    # ----- files
    def load(self):
        if not os.path.exists(self.path(USER_FILE)):
            self.users = {ADMIN: hash_password(DEFAULT_ADMIN_PASSWORD)}
            self.save_users()
        else:
            self.users = {}
            for line in self._read_lines(USER_FILE):
                username, _, password = line.partition(";")
                if username:
                    self.users[username.strip()] = password.strip()
        self.tasks = []
        for line in self._read_lines(TASK_FILE):
            try:
                self.tasks.append(Task.from_line(line))
            except (ValueError, TaskManagerError):
                print(f"Warning: skipped a damaged line in {TASK_FILE}: {line}")

    def _read_lines(self, name):
        if not os.path.exists(self.path(name)):
            return []
        with open(self.path(name), encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f if line.strip()]

    def _write_lines(self, name, lines):
        # write to a temporary file first, so a crash never leaves a half-written file
        tmp = self.path(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, self.path(name))

    def save_users(self):
        self._write_lines(USER_FILE, [f"{u};{p}" for u, p in self.users.items()])

    def save_tasks(self):
        self._write_lines(TASK_FILE, [t.to_line() for t in self.tasks])

    # ----- users
    def login(self, username, password):
        stored = self.users.get(username)
        if stored is None or not check_password(password, stored):
            return False
        if not stored.startswith("pbkdf2$"):          # upgrade an old plain password
            self.users[username] = hash_password(password)
            self.save_users()
        return True

    def _check_new_password(self, password, confirm):
        if password != confirm:
            raise TaskManagerError("Passwords do not match.")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise TaskManagerError(f"Password must have at least {MIN_PASSWORD_LENGTH} characters.")
        clean(password, "Password")

    def register(self, username, password, confirm):
        username = clean(username, "Username")
        if " " in username:
            raise TaskManagerError("Username cannot contain spaces.")
        if username.lower() in (u.lower() for u in self.users):
            raise TaskManagerError("Username already exists.")
        self._check_new_password(password, confirm)
        self.users[username] = hash_password(password)
        self.save_users()

    def change_password(self, username, old, new, confirm):
        if not self.login(username, old):
            raise TaskManagerError("Current password is wrong.")
        self._check_new_password(new, confirm)
        self.users[username] = hash_password(new)
        self.save_users()

    # ----- tasks
    def add_task(self, username, title, description, due_date, today=None):
        if username not in self.users:
            raise TaskManagerError("User does not exist.")
        today = today or date.today()
        if due_date < today:
            raise TaskManagerError("Due date cannot be in the past.")
        task = Task(username, clean(title, "Title"), clean(description, "Description", allow_empty=True),
                    due_date, today)
        self.tasks.append(task)
        self.save_tasks()
        return len(self.tasks)          # task number shown to the user

    def get(self, number):
        if not 1 <= number <= len(self.tasks):
            raise TaskManagerError("There is no task with this number.")
        return self.tasks[number - 1]

    def numbered(self, username=None, only_overdue=False):
        """(number, task) pairs; number = position in tasks.txt, starting at 1."""
        return [(i, t) for i, t in enumerate(self.tasks, start=1)
                if (username is None or t.username == username)
                and (not only_overdue or t.is_overdue())]

    def can_change(self, number, user):
        return user == ADMIN or self.get(number).username == user

    def mark_complete(self, number):
        task = self.get(number)
        if task.completed:
            raise TaskManagerError("This task is already completed.")
        task.completed = True
        self.save_tasks()

    def edit_task(self, number, username=None, due_date=None, title=None, description=None):
        task = self.get(number)
        if task.completed:
            raise TaskManagerError("A completed task cannot be edited.")
        if username is not None:
            if username not in self.users:
                raise TaskManagerError("User does not exist.")
            task.username = username
        if due_date is not None:
            task.due_date = due_date
        if title is not None:
            task.title = clean(title, "Title")
        if description is not None:
            task.description = clean(description, "Description", allow_empty=True)
        self.save_tasks()

    def delete_task(self, number):
        task = self.get(number)
        self.tasks.remove(task)
        self.save_tasks()
        return task

    # ----- reports
    def generate_reports(self, today=None):
        today = today or date.today()
        total = len(self.tasks)
        done = sum(t.completed for t in self.tasks)
        overdue = sum(t.is_overdue(today) for t in self.tasks)

        task_lines = [
            f"Report date: {today.strftime(DATE_FORMAT)}",
            f"Total number of tasks: {total}",
            f"Total number of completed tasks: {done}",
            f"Total number of uncompleted tasks: {total - done}",
            f"Total number of tasks that are overdue: {overdue}",
            f"Percentage of tasks that are incomplete: {percent(total - done, total)}",
            f"Percentage of tasks that are overdue: {percent(overdue, total)}",
        ]
        user_lines = [f"Report date: {today.strftime(DATE_FORMAT)}",
                      f"Total number of users: {len(self.users)}",
                      f"Total number of tasks: {total}"]
        for user in self.users:
            mine = [t for t in self.tasks if t.username == user]
            mine_done = sum(t.completed for t in mine)
            mine_overdue = sum(t.is_overdue(today) for t in mine)
            user_lines += ["",
                           f"User: {user}",
                           f"Total number of tasks assigned: {len(mine)}",
                           f"Percentage of total tasks assigned: {percent(len(mine), total)}",
                           f"Percentage of tasks completed: {percent(mine_done, len(mine))}",
                           f"Percentage of tasks incomplete: {percent(len(mine) - mine_done, len(mine))}",
                           f"Percentage of tasks overdue: {percent(mine_overdue, len(mine))}"]
        self._write_lines(TASK_REPORT, task_lines)
        self._write_lines(USER_REPORT, user_lines)
        return "\n".join(task_lines), "\n".join(user_lines)


# ---------------------------------------------------------------- menu (console)

def ask(prompt):
    return input(prompt).strip()


def ask_password(prompt):
    # getpass hides the password; it falls back to normal input in some IDEs (e.g. IDLE)
    try:
        return getpass.getpass(prompt)
    except (EOFError, OSError):
        return input(prompt)


def ask_date(prompt, allow_empty=False):
    while True:
        text = ask(prompt)
        if allow_empty and not text:
            return None
        try:
            return parse_date(text)
        except TaskManagerError as e:
            print(e)


def ask_number(prompt):
    while True:
        text = ask(prompt)
        if text.lstrip("-").isdigit():
            return int(text)
        print("Please enter a number.")


def login_screen(tm):
    print("=" * 40 + "\n         TASK MANAGER - LOGIN\n" + "=" * 40)
    for attempt in range(3, 0, -1):
        username = ask("Username: ")
        password = ask_password("Password: ")
        if tm.login(username, password):
            print(f"\nWelcome, {username}!")
            return username
        print(f"Wrong username or password. Attempts left: {attempt - 1}")
    print("Too many failed attempts. Goodbye.")
    sys.exit(1)


def show_tasks(pairs, empty_message):
    if not pairs:
        print(empty_message)
    for number, task in pairs:
        print(task.details(number))


def menu_register(tm):
    try:
        tm.register(ask("New username: "), ask_password("New password: "), ask_password("Confirm password: "))
        print("New user added.")
    except TaskManagerError as e:
        print(e)


def menu_add_task(tm):
    username = ask("Username of the person assigned to the task: ")
    while username not in tm.users:
        print("User does not exist. Users: " + ", ".join(tm.users))
        username = ask("Username of the person assigned to the task: ")
    title = ask("Title of task: ")
    description = ask("Description of task: ")
    while True:
        try:
            number = tm.add_task(username, title, description, ask_date("Due date (YYYY-MM-DD): "))
            print(f"Task {number} successfully added.")
            return
        except TaskManagerError as e:
            print(e)
            if "date" not in str(e).lower():
                return


def menu_my_tasks(tm, user):
    while True:
        pairs = tm.numbered(user)
        show_tasks(pairs, "No tasks assigned to you.")
        if not pairs:
            return
        number = ask_number("Enter a task number to complete/edit/delete (-1 = back to menu): ")
        if number == -1:
            return
        if number not in [n for n, _ in pairs]:
            print("That is not one of your tasks.")
            continue
        task_action(tm, user, number)


def task_action(tm, user, number):
    task = tm.get(number)
    print(task.details(number))
    action = ask("c = mark complete, e = edit, d = delete, anything else = back: ").lower()
    try:
        if action == "c":
            tm.mark_complete(number)
            print("Task marked as complete.")
        elif action == "e":
            if task.completed:
                raise TaskManagerError("A completed task cannot be edited.")
            print("Press Enter to keep the current value.")
            new_user = ask(f"Assigned to [{task.username}]: ") or None
            new_due = ask_date(f"Due date [{task.due_date}]: ", allow_empty=True)
            new_title = ask(f"Title [{task.title}]: ") or None
            new_desc = ask("Description [keep]: ") or None
            tm.edit_task(number, new_user, new_due, new_title, new_desc)
            print("Task updated.")
        elif action == "d":
            if ask(f"Delete task '{task.title}'? (y/n): ").lower() == "y":
                tm.delete_task(number)
                print("Task deleted.")
    except TaskManagerError as e:
        print(e)


def menu_all_tasks(tm, user):
    show_tasks(tm.numbered(), "There are no tasks yet.")
    if user == ADMIN and tm.tasks:
        number = ask_number("Admin: enter a task number to manage it (-1 = back): ")
        if number != -1:
            try:
                tm.get(number)
                task_action(tm, user, number)
            except TaskManagerError as e:
                print(e)


def menu_change_password(tm, user):
    try:
        tm.change_password(user, ask_password("Current password: "),
                           ask_password("New password: "), ask_password("Confirm new password: "))
        print("Password changed.")
    except TaskManagerError as e:
        print(e)


def menu_statistics(tm):
    task_text, user_text = tm.generate_reports()   # always fresh numbers
    print("\nTASK OVERVIEW\n" + "-" * 30 + "\n" + task_text)
    print("\nUSER OVERVIEW\n" + "-" * 30 + "\n" + user_text)


def main(folder="."):
    tm = TaskManager(folder)
    user = login_screen(tm)
    if user == ADMIN and tm.login(ADMIN, DEFAULT_ADMIN_PASSWORD):
        print("Tip: you are using the default admin password. Change it with 'cp'.")

    while True:
        options = ["a  - Add a task", "va - View all tasks", "vm - View my tasks",
                   "vo - View overdue tasks", "cp - Change my password"]
        if user == ADMIN:
            options += ["r  - Register a user", "g  - Generate reports", "ds - Display statistics"]
        options.append("e  - Exit")
        choice = ask("\nSelect an option:\n" + "\n".join(options) + "\n: ").lower()

        if choice == "a":
            menu_add_task(tm)
        elif choice == "va":
            menu_all_tasks(tm, user)
        elif choice == "vm":
            menu_my_tasks(tm, user)
        elif choice == "vo":
            show_tasks(tm.numbered(None if user == ADMIN else user, only_overdue=True), "No overdue tasks.")
        elif choice == "cp":
            menu_change_password(tm, user)
        elif choice == "r" and user == ADMIN:
            menu_register(tm)
        elif choice == "g" and user == ADMIN:
            tm.generate_reports()
            print(f"Reports saved to {TASK_REPORT} and {USER_REPORT}.")
        elif choice == "ds" and user == ADMIN:
            menu_statistics(tm)
        elif choice == "e":
            print("Goodbye!")
            break
        elif choice in ("r", "g", "ds"):
            print("Only the admin can use this option.")
        else:
            print("You have made a wrong choice. Please try again.")


if __name__ == "__main__":
    try:
        main(os.path.dirname(os.path.abspath(__file__)))
    except KeyboardInterrupt:
        print("\nGoodbye!")
