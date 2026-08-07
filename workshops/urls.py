from django.urls import path

from . import views

app_name = "workshops"

urlpatterns = [
    path("workshop/libraries", views.libraries, name="libraries"),
    path(
        "workshop/libraries/<str:family>/create",
        views.library_create,
        name="library-create",
    ),
    path(
        "workshop/libraries/<str:family>/<int:item_id>/edit",
        views.library_edit,
        name="library-edit",
    ),
    path(
        "workshop/libraries/<str:family>/<int:item_id>/<str:action>",
        views.library_transition,
        name="library-transition",
    ),
]
