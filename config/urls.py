"""Root URL configuration.

F1 exposes only a health/root route; feature routes arrive with later slices
(F2 replaces the root route with the real base-template home page).
"""

from django.urls import path

from config.views import health

urlpatterns = [
    path("", health, name="root"),
    path("health/", health, name="health"),
]
