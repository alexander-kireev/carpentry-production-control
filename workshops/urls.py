from django.urls import path

from . import views

app_name = "workshops"

urlpatterns = [
    path("workshop/materials", views.materials, name="materials"),
    path("workshop/materials/create", views.material_create, name="material-create"),
    path(
        "workshop/materials/<int:material_id>",
        views.material_detail,
        name="material-detail",
    ),
    path(
        "workshop/materials/<int:material_id>/edit",
        views.material_edit,
        name="material-edit",
    ),
    path(
        "workshop/materials/<int:material_id>/<str:action>",
        views.material_transition,
        name="material-transition",
    ),
    path(
        "workshop/materials/<int:material_id>/variants/create",
        views.material_variant_create,
        name="material-variant-create",
    ),
    path(
        "workshop/materials/variants/<int:variant_id>",
        views.material_variant_detail,
        name="material-variant-detail",
    ),
    path(
        "workshop/materials/variants/<int:variant_id>/edit",
        views.material_variant_edit,
        name="material-variant-edit",
    ),
    path(
        "workshop/materials/variants/<int:variant_id>/<str:action>",
        views.material_variant_transition,
        name="material-variant-transition",
    ),
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
