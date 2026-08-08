SESSION_KEY = "foundation_feedback"


def set_feedback(request, *, kind="success", title, body):
    """Store one presentation-safe ordinary feedback item for the next GET."""
    if kind not in {"success", "info"}:
        raise ValueError("ordinary feedback kind must be success or info")
    request.session[SESSION_KEY] = {
        "kind": kind,
        "title": str(title),
        "body": str(body),
    }


def pop_feedback(request):
    return request.session.pop(SESSION_KEY, None)
