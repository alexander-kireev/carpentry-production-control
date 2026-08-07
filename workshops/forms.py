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
        required=False, coerce=int, choices=()
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
