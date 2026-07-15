"""Seed the permanent, workshop-independent system records (D0-3).

Data migration for the "undefined" StationCategory / MaterialCategory /
WorkshopRole sentinels and the "Admin" WorkshopRole — all with a NULL workshop
so they exist before the first Workshop (KI-012). Idempotent and reversible; the
seed logic lives in ``catalog/seeds.py`` so it is unit-testable and shared with
the slices that consume these rows.
"""

from django.db import migrations

from catalog.seeds import seed_system_records, unseed_system_records


def forwards(apps, schema_editor):
    seed_system_records(apps)


def backwards(apps, schema_editor):
    unseed_system_records(apps)


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
