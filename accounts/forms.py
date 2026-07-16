"""Admin self-registration form (A1).

``AdminRegisterForm`` mirrors Django's built-in ``UserCreationForm`` shape
(password1/password2 + ``set_password`` on save) but is a ``ModelForm`` over
this project's email-login ``User`` and its domain fields (first/last name,
date_of_birth) rather than username. It owns only the fields the admin
actually submits; ``account_role``/``status``/``workshop_role`` are assigned
by ``accounts.services.register_admin``, not here.
"""

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.models import User
from accounts.services import IDENTITY_FIELD_LABELS, IDENTITY_FIELDS


class AdminRegisterForm(forms.ModelForm):
    """Public register form for the single self-registering admin (Slice A)."""

    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "date_of_birth", "email"]

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise ValidationError("Passwords do not match.")
        return password2

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password1")
        if password:
            # ModelForm only copies cleaned data onto self.instance in
            # _post_clean, which runs *after* this method — so build a
            # throwaway User from cleaned_data here rather than reading
            # self.instance, which would still be blank. This is what lets
            # UserAttributeSimilarityValidator compare the password against
            # the name/email actually submitted on this form.
            dummy = User(
                email=cleaned_data.get("email", ""),
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
            )
            try:
                validate_password(password, dummy)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class ProfilePhoneForm(forms.ModelForm):
    """The owner's inline phone edit on the Profile page (Slice D / D2).

    Phone is the only freely self-editable profile field (no approval step); a
    thin ``ModelForm`` over ``User.phone`` gives the field its ``max_length``
    validation. Blank is allowed — clearing the number is a legitimate edit.
    The actual write goes through ``accounts.services.set_own_phone``.
    """

    class Meta:
        model = User
        fields = ["phone"]
        widgets = {
            "phone": forms.TextInput(
                attrs={
                    "type": "tel",
                    "class": "form-control form-control-sm",
                    "placeholder": "Add a phone number",
                }
            ),
        }


class ChangeRequestForm(forms.Form):
    """A non-admin's identity Request-change submission (Slice D / D3).

    Not a ``ModelForm`` — it maps to the ``submit_cr`` service, not directly to
    ``ChangeRequest`` columns. The "New value" field adapts to the target field
    (text for names, a native date input for ``date_of_birth``); ``reason`` is
    mandatory. The bound ``target_field`` comes from a hidden input on the
    per-field modal and is validated against the requestable identity set.
    """

    reason = forms.CharField(
        label="Reason",
        widget=forms.Textarea(
            attrs={"rows": 2, "class": "form-control form-control-sm"}
        ),
        help_text="Why are you requesting this change?",
    )

    def __init__(self, *args, target_field=None, **kwargs):
        super().__init__(*args, **kwargs)
        if target_field not in IDENTITY_FIELDS:
            raise ValueError(f"{target_field!r} is not a requestable identity field.")
        self.target_field = target_field
        self.fields["proposed_value"] = self._value_field(target_field)
        # Render the new-value field above the reason.
        self.order_fields(["proposed_value", "reason"])

    @staticmethod
    def _value_field(target_field):
        label = f"New {IDENTITY_FIELD_LABELS[target_field].lower()}"
        if target_field == "date_of_birth":
            return forms.DateField(
                label=label,
                widget=forms.DateInput(
                    attrs={"type": "date", "class": "form-control form-control-sm"},
                    format="%Y-%m-%d",
                ),
            )
        return forms.CharField(
            label=label,
            max_length=150,
            widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
        )


class RejectReasonForm(forms.Form):
    """The mandatory reason an admin gives when rejecting a CR (Slice D / D3)."""

    note = forms.CharField(
        label="Reason for rejection",
        widget=forms.Textarea(
            attrs={"rows": 2, "class": "form-control form-control-sm"}
        ),
    )


class AdminIdentityForm(forms.ModelForm):
    """An admin's direct edit of their own identity fields (Slice D / D3).

    A ``ModelForm`` over ``User`` for the field widgets/validation and the
    prefilled current values; the mandatory ``reason`` is an extra field. The
    view does not call ``save()`` — it diffs the cleaned values against the
    stored ones and routes each changed field through
    ``accounts.services.apply_identity_change`` (which carries the reason and the
    supersede behaviour), so the write path is identical to Slice B's Edit User panel.
    """

    reason = forms.CharField(
        label="Reason",
        widget=forms.Textarea(
            attrs={"rows": 2, "class": "form-control form-control-sm"}
        ),
        help_text="Recorded with the change.",
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "date_of_birth"]
        widgets = {
            "first_name": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
            "date_of_birth": forms.DateInput(
                attrs={"type": "date", "class": "form-control form-control-sm"},
                format="%Y-%m-%d",
            ),
        }
