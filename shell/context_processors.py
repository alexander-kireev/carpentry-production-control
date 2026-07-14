"""Template context for the shell frame: the effective role and the DEBUG flag.

``effective_role`` drives which nav renders; ``debug`` gates the "view as role"
switcher in the navbar.
"""

from django.conf import settings

from shell.roles import get_effective_role


def effective_role(request):
    return {
        "effective_role": get_effective_role(request),
        "debug": settings.DEBUG,
    }
