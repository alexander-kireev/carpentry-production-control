from django import forms
from django.contrib.auth import password_validation

from .models import User


class RegistrationForm(forms.Form):
    submission_nonce = forms.CharField(widget=forms.HiddenInput)
    first_name = forms.CharField(
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    date_of_birth = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "autocomplete": "bday"})
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    password_confirmation = forms.CharField(
        strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    activation_code = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "one-time-code",
                "aria-describedby": "code-help",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("password_confirmation"):
            self.add_error("password_confirmation", "The passwords do not match.")
        if cleaned.get("password"):
            candidate = User(
                first_name=cleaned.get("first_name", ""),
                last_name=cleaned.get("last_name", ""),
                email=cleaned.get("email", ""),
            )
            try:
                password_validation.validate_password(cleaned["password"], candidate)
            except forms.ValidationError as error:
                self.add_error("password", error)
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
