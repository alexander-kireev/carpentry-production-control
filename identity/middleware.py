from django.contrib.auth import logout
from django.shortcuts import redirect

from .queries import get_onboarding_page_access, resolve_authenticated_destination


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
        allowed = {"/", "/logout", "/onboarding", resolution.destination.value}
        if path in {"/onboarding/workshop", "/onboarding/manager", "/onboarding/setup"}:
            if path == resolution.destination.value:
                return self.get_response(request)
            access = get_onboarding_page_access(request.user)
            if access is not None:
                if path != "/onboarding/setup" or access["setup_available"]:
                    return self.get_response(request)
            return redirect(resolution.destination.value)
        if path == "/workshop/libraries" or path.startswith("/workshop/libraries/"):
            from workshops.queries import resolve_libraries_access

            if resolve_libraries_access(request.user) is not None:
                return self.get_response(request)
        if path == "/workshop/materials" or path.startswith("/workshop/materials/"):
            from workshops.queries import resolve_materials_access

            if resolve_materials_access(request.user) is not None:
                return self.get_response(request)
        if path == "/workshop/stations" or path.startswith("/workshop/stations/"):
            from workshops.queries import resolve_stations_access

            if resolve_stations_access(request.user) is not None:
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
