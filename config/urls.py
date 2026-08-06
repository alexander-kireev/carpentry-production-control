"""URL configuration for the local Django foundation."""

from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

from foundation.views import health
from identity.views import root_destination

urlpatterns = [
    path("", root_destination, name="root"),
    path("health/", health, name="health"),
    path("", include("identity.urls")),
]

urlpatterns += staticfiles_urlpatterns()
