"""Catalog URLs (C1): library import action + read-only table fragment.

Flat routes, no trailing slash (D-123). ``<type>`` is a library-type slug
(``operation-type``, ``unit-type``, ...); an unknown slug 404s in the view.
"""

from django.urls import path

from catalog import views

app_name = "catalog"

urlpatterns = [
    path("library/<slug:slug>", views.library_table, name="library_table"),
    path("library/<slug:slug>/import", views.library_import, name="library_import"),
]
