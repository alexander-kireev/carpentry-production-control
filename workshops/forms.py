import json

from django import forms


class BaseLibraryForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        if edit:
            self.fields.pop("submission_key")


class WorkshopRoleForm(BaseLibraryForm):
    name = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea)
    default_clearance_ids = forms.TypedMultipleChoiceField(
        required=False,
        coerce=int,
        choices=(),
        widget=forms.CheckboxSelectMultiple(attrs={"class": "library-checkbox-list"}),
    )

    def __init__(self, *args, clearance_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_clearance_ids"].choices = clearance_choices


class OperationTypeForm(BaseLibraryForm):
    name = forms.CharField(max_length=200)
    description = forms.CharField(required=False, widget=forms.Textarea)
    is_production = forms.BooleanField(required=False)
    requires_clearance = forms.BooleanField(required=False)


class UnitTypeForm(BaseLibraryForm):
    name = forms.CharField(max_length=200)
    abbreviation = forms.CharField(max_length=30)


class MaterialCategoryForm(BaseLibraryForm):
    name = forms.CharField(max_length=200)


class ShiftDefinitionForm(BaseLibraryForm):
    name = forms.CharField(max_length=200)
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    days = forms.TypedMultipleChoiceField(
        coerce=int,
        choices=tuple(
            (day, label)
            for day, label in enumerate(
                (
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                )
            )
        ),
    )

    def clean_days(self):
        days = sorted(set(self.cleaned_data["days"]))
        if not days:
            raise forms.ValidationError("Select at least one day.")
        return days

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start is not None and end is not None and start >= end:
            self.add_error("end_time", "End time must be later on the same day.")
        return cleaned


FORM_CLASSES = {
    "workshop_role": WorkshopRoleForm,
    "operation_type": OperationTypeForm,
    "unit_type": UnitTypeForm,
    "material_category": MaterialCategoryForm,
    "shift_definition": ShiftDefinitionForm,
}


class MaterialForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    name = forms.CharField(max_length=200)
    category_id = forms.TypedChoiceField(coerce=int, choices=())
    category_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    unit_id = forms.TypedChoiceField(coerce=int, choices=())
    unit_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    spec_label = forms.CharField(max_length=200, required=False)
    opening_quantity = forms.DecimalField(
        min_value=0, max_digits=14, decimal_places=4, required=False
    )
    min_threshold = forms.DecimalField(
        min_value=0, max_digits=14, decimal_places=4, required=False
    )

    def __init__(self, *args, categories=(), units=(), edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category_id"].choices = [
            (row["id"], row["label"]) for row in categories
        ]
        self.fields["unit_id"].choices = [
            (row["id"], f"{row['label']} ({row['abbreviation']})") for row in units
        ]
        self.category_versions = {str(row["id"]): row["version"] for row in categories}
        self.unit_versions = {str(row["id"]): row["version"] for row in units}
        self.fields["category_id"].widget.attrs["data-version-map"] = json.dumps(
            self.category_versions, separators=(",", ":")
        )
        self.fields["unit_id"].widget.attrs["data-version-map"] = json.dumps(
            self.unit_versions, separators=(",", ":")
        )
        if edit:
            self.fields.pop("spec_label")
            self.fields.pop("opening_quantity")
            self.fields.pop("min_threshold")

    def clean(self):
        cleaned = super().clean()
        variant_values = [
            cleaned.get("spec_label"),
            cleaned.get("opening_quantity"),
            cleaned.get("min_threshold"),
        ]
        if any(value not in (None, "") for value in variant_values) and not all(
            value not in (None, "") for value in variant_values
        ):
            self.add_error(
                "spec_label", "Complete all first Variant fields or leave all blank."
            )
        return cleaned


class MaterialVariantForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    material_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    spec_label = forms.CharField(max_length=200)
    opening_quantity = forms.DecimalField(min_value=0, max_digits=14, decimal_places=4)
    min_threshold = forms.DecimalField(min_value=0, max_digits=14, decimal_places=4)

    def __init__(self, *args, edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        if edit:
            self.fields.pop("material_version")
            self.fields.pop("opening_quantity")


class MaterialTransitionForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class StationForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    name = forms.CharField(max_length=200, label="Station name")
    capability_ids = forms.TypedMultipleChoiceField(
        required=False,
        coerce=int,
        choices=(),
        label="Supported Operation Types",
        help_text="Select zero or more capabilities. Other is included only when checked.",
        widget=forms.CheckboxSelectMultiple(attrs={"class": "library-checkbox-list"}),
    )

    def __init__(self, *args, capability_choices=(), edit=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["capability_ids"].choices = capability_choices
        if edit:
            self.fields.pop("submission_key")


class StationRetireForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
