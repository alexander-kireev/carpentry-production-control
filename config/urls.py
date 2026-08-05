"""URL configuration for the local Django foundation."""

from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import path

from foundation.views import health, root

urlpatterns = [
    path("", root, name="root"),
    path("health/", health, name="health"),
]

urlpatterns += staticfiles_urlpatterns()
