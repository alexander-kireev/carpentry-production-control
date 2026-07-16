"""Root URL configuration.

S1 adds role-based routing: authenticated users dispatch to their role landing.
Login/logout remain at top level. Shell sub-routes extend this.
"""

from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from accounts.views import register
from config.views import health
from shell.views import root

urlpatterns = [
    path("", root, name="root"),
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("register", register, name="register"),
    path("", include("workshop.urls")),
    path("", include("catalog.urls")),
    path("", include("accounts.urls")),
    path("health/", health, name="health"),
    path("", include("shell.urls")),
]
