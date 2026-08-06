from django.db import migrations

OTHER_DESCRIPTION = (
    "Generic catch-all for flexible scheduling not tied to a specific skill "
    "or production step."
)


def seed_protected_identities(apps, schema_editor):
    WorkshopRole = apps.get_model("workshops", "WorkshopRole")
    OperationType = apps.get_model("workshops", "OperationType")
    database = schema_editor.connection.alias

    expected_roles = (
        {"machine_key": "undefined", "name": "undefined"},
        {"machine_key": "admin", "name": "Admin"},
    )
    for expected in expected_roles:
        role, _ = WorkshopRole.objects.using(database).get_or_create(
            machine_key=expected["machine_key"],
            defaults={
                "workshop_id": None,
                "name": expected["name"],
                "description": None,
                "status": "active",
                "version": 1,
            },
        )
        if not (
            role.workshop_id is None
            and role.name == expected["name"]
            and role.status == "active"
            and role.version > 0
        ):
            raise RuntimeError("Protected workshop role bootstrap conflict")

    other, _ = OperationType.objects.using(database).get_or_create(
        machine_key="other",
        defaults={
            "workshop_id": None,
            "name": "Other",
            "description": OTHER_DESCRIPTION,
            "is_production": True,
            "requires_clearance": False,
            "status": "active",
            "version": 1,
        },
    )
    if not (
        other.workshop_id is None
        and other.name == "Other"
        and other.status == "active"
        and other.is_production is True
        and other.requires_clearance is False
        and other.version > 0
    ):
        raise RuntimeError("Protected operation type bootstrap conflict")

    roles = WorkshopRole.objects.using(database).filter(workshop_id__isnull=True)
    operation_types = OperationType.objects.using(database).filter(
        workshop_id__isnull=True
    )
    if roles.count() != 2 or operation_types.count() != 1:
        raise RuntimeError("Protected configuration bootstrap is not exact")


def preserve_protected_identities(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("workshops", "0002_database_guards"),
        ("identity", "0002_database_guards"),
    ]

    operations = [
        migrations.RunPython(seed_protected_identities, preserve_protected_identities)
    ]
