from __future__ import annotations

import os
from functools import wraps

from dotenv import load_dotenv
from flask import redirect, session, url_for


load_dotenv()


def expected_username() -> str:
    return os.getenv("ADMIN_USERNAME", "admin")


def expected_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "admin123")


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_authenticated"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view
