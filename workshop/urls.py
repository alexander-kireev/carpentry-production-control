"""Workshop app URLs (A2). Flat route, no trailing slash (D-123)."""

from django.urls import path

from workshop.views import setup

urlpatterns = [
    path("workshop/setup", setup, name="workshop_setup"),
]
