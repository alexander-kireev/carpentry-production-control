"""Admin self-registration view (A1; opened up in A2 per D-126): public ``/register``.

Always available — each registration creates a new, independent admin for a new
workshop (D-126); there is no lockout state. The freshly-created admin has no
workshop yet, so the setup-gate middleware routes them through Workshop setup.
"""

from django.contrib.auth import login
from django.shortcuts import redirect, render

from accounts.forms import AdminRegisterForm
from accounts.services import register_admin


def register(request):
    if request.method == "POST":
        form = AdminRegisterForm(request.POST)
        if form.is_valid():
            user = register_admin(form)
            # The user was created via set_password (not authenticate()), so it
            # has no .backend attribute; login() requires one.
            user.backend = "django.contrib.auth.backends.ModelBackend"
            login(request, user)
            return redirect("/workshop/setup")
    else:
        form = AdminRegisterForm()

    return render(request, "registration/register.html", {"form": form})
