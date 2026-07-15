"""Template helpers for the generic library table (C1)."""

from django import template

register = template.Library()


@register.filter
def model_field(obj, key):
    """Read ``getattr(obj, key)`` so one table template can render any type.

    The library table is generic over a config-driven column list, so the cell
    value comes from a field name held in a variable — which Django's dot lookup
    can't do directly.
    """
    return getattr(obj, key, "")
