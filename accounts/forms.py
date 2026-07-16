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
