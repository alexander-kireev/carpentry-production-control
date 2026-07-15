"""Workshop setup form (A2).

``WorkshopSetupForm`` is a plain ``ModelForm`` over the three admin-supplied
Workshop fields (name / address / email). Tying the Workshop to the creating
admin is the service's job (``workshop.services.create_workshop``), not the
form's — mirroring how ``AdminRegisterForm`` leaves role/status assignment to
``accounts.services.register_admin``.
"""

from django import forms

from catalog.models import Workshop


class WorkshopSetupForm(forms.ModelForm):
    """The gate's target form: create the requesting admin's Workshop."""

    class Meta:
        model = Workshop
        fields = ["name", "address", "email"]
