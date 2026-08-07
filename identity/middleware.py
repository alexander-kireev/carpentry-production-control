from django.contrib.auth import logout
from django.shortcuts import redirect

from .queries import resolve_authenticated_destination


class PreWorkshopAccessMiddleware:
    public_paths = {"/", "/login", "/register", "/health/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith("/invitations/"):
            return self.get_response(request)
        if path == "/health/":
            return self.get_response(request)
        if path.startswith("/static/"):
            return self.get_response(request)
        if not request.user.is_authenticated:
            if path not in self.public_paths:
                return redirect("login")
            return self.get_response(request)
        resolution = resolve_authenticated_destination(request.user)
        if not resolution.supported:
            logout(request)
            return redirect("login")
        request.identity_destination = resolution
        allowed = {"/", "/logout", resolution.destination.value}
        if path == "/workshop/libraries" or path.startswith("/workshop/libraries/"):
            from workshops.queries import resolve_libraries_access

            if resolve_libraries_access(request.user) is not None:
                return self.get_response(request)
        if path not in allowed:
            return redirect(resolution.destination.value)
        return self.get_response(request)


class InvitationCredentialResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/invitations/"):
            response["Referrer-Policy"] = "origin"
            response["Cache-Control"] = "no-store, private"
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response
