from zoneinfo import available_timezones

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


class WorkshopCreationForm(forms.Form):
    submission_nonce = forms.CharField(widget=forms.HiddenInput)
    expected_user_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    name = forms.CharField(
        strip=True,
        label="Workshop name",
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    address = forms.CharField(
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3, "autocomplete": "street-address"}),
    )
    contact_email = forms.EmailField(
        label="Contact email",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    timezone = forms.ChoiceField(choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        zones = sorted(available_timezones())
        self.fields["timezone"].choices = [("", "Select an IANA timezone")] + [
            (zone, zone) for zone in zones
        ]

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if not value:
            raise forms.ValidationError("Enter a Workshop name.")
        return value

    def clean_address(self):
        value = self.cleaned_data["address"].strip()
        if not value:
            raise forms.ValidationError("Enter an address.")
        return value

    def clean_contact_email(self):
        return self.cleaned_data["contact_email"].strip().casefold()

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        if value not in available_timezones():
            raise forms.ValidationError("Select a recognised IANA timezone.")
        return value


class PermanentManagerInvitationForm(forms.Form):
    submission_nonce = forms.CharField(widget=forms.HiddenInput)
    expected_workshop_version = forms.IntegerField(
        min_value=1, widget=forms.HiddenInput
    )
    first_name = forms.CharField(
        strip=True, widget=forms.TextInput(attrs={"autocomplete": "given-name"})
    )
    last_name = forms.CharField(
        strip=True, widget=forms.TextInput(attrs={"autocomplete": "family-name"})
    )
    date_of_birth = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "autocomplete": "bday"})
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))

    def clean_first_name(self):
        value = self.cleaned_data["first_name"].strip()
        if not value:
            raise forms.ValidationError("Enter the manager's first name.")
        return value

    def clean_last_name(self):
        value = self.cleaned_data["last_name"].strip()
        if not value:
            raise forms.ValidationError("Enter the manager's last name.")
        return value

    def clean_email(self):
        return self.cleaned_data["email"].strip().casefold()


class WorkshopTimezoneCorrectionForm(forms.Form):
    timezone_action = forms.CharField(widget=forms.HiddenInput, initial="correct")
    submission_nonce = forms.CharField(widget=forms.HiddenInput)
    expected_workshop_version = forms.IntegerField(
        min_value=1, widget=forms.HiddenInput
    )
    timezone = forms.ChoiceField(choices=(), label="New timezone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        zones = sorted(available_timezones())
        self.fields["timezone"].choices = [("", "Select an IANA timezone")] + [
            (zone, zone) for zone in zones
        ]

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        if value not in available_timezones():
            raise forms.ValidationError("Select a recognised IANA timezone.")
        return value
