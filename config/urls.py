"""Root URL configuration.

F2 adds the base-template home page plus email login/logout. Role-specific
page-trees arrive with the App-shell slice (S1). ``/health/`` stays as the
container/health-check endpoint.
"""

from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from config.views import health, home

urlpatterns = [
    path("", home, name="root"),
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
]
