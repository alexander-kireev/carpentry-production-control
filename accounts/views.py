"""Admin self-registration view (A1): public ``/register``.

Refuses (renders a lockout state) once an admin exists, both on GET (so the
form never shows) and on POST (defense-in-depth against the service's own
guard firing on a race).
"""

from django.contrib.auth import login
from django.shortcuts import redirect, render

from accounts.forms import AdminRegisterForm
from accounts.models import User
from accounts.services import AdminExistsError, register_admin


def register(request):
    if User.objects.filter(account_role=User.AccountRole.ADMIN).exists():
        return render(request, "registration/register.html", {"locked": True})

    if request.method == "POST":
        form = AdminRegisterForm(request.POST)
        if form.is_valid():
            try:
                user = register_admin(form)
            except AdminExistsError:
                return render(request, "registration/register.html", {"locked": True})
            user.backend = "django.contrib.auth.backends.ModelBackend"
            login(request, user)
            return redirect("/workshop/setup")
    else:
        form = AdminRegisterForm()

    return render(request, "registration/register.html", {"form": form, "locked": False})
