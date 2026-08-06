"""URL configuration for the local Django foundation."""

from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

from foundation.views import health, root

urlpatterns = [
    path("", root, name="root"),
    path("health/", health, name="health"),
    path("", include("identity.urls")),
]

urlpatterns += staticfiles_urlpatterns()
