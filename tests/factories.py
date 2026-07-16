"""Test factories.

``UserFactory`` (F2) plus the catalog factories introduced in D0-1: Workshop and
the nine library/reference models. Sub-factories share a single Workshop via
``SelfAttribute`` so a Material's category/unit (and a Station's category) live in
the same workshop as their parent.
"""

import datetime
from decimal import Decimal

import factory
from django.utils import timezone

from accounts.models import ChangeRequest, User
from catalog.models import (
    Material,
    MaterialCategory,
    MaterialVariant,
    OperationType,
    ShiftDefinition,
    Station,
    StationCategory,
    UnitType,
    Workshop,
    WorkshopRole,
)

# Known password so tests can authenticate the built users.
DEFAULT_PASSWORD = "workshop-pass-123"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    account_role = User.AccountRole.TECHNICIAN
    password = factory.django.Password(DEFAULT_PASSWORD)
    # Required domain field (D0-2); a fixed, obviously-synthetic DOB.
    date_of_birth = datetime.date(1990, 1, 1)
    # A workshop member is the normal steady state under D-126, and the real
    # setup-gate (A2) redirects any workshop-less authenticated user — so the
    # factory default gives the user a workshop, with their workshop_role scoped
    # to that same workshop (the StationFactory pattern below). Tests that need
    # the transient pre-setup admin pass `workshop=None` explicitly. String
    # references because WorkshopFactory / WorkshopRoleFactory are defined later
    # in this module.
    workshop = factory.SubFactory("tests.factories.WorkshopFactory")
    workshop_role = factory.SubFactory(
        "tests.factories.WorkshopRoleFactory",
        workshop=factory.SelfAttribute("..workshop"),
    )

    @factory.post_generation
    def clearances(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        self.clearances.add(*extracted)


class WorkshopFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Workshop

    name = factory.Sequence(lambda n: f"Workshop {n}")
    address = factory.Sequence(lambda n: f"{n} Timber Lane")
    email = factory.Sequence(lambda n: f"workshop{n}@example.com")


class OperationTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OperationType

    workshop = factory.SubFactory(WorkshopFactory)
    name = factory.Sequence(lambda n: f"Operation {n}")
    is_production = True


class UnitTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UnitType

    workshop = factory.SubFactory(WorkshopFactory)
    # Defaults evoke a countable unit (Piece/pc); the sequence keeps them unique.
    name = factory.Sequence(lambda n: f"Piece {n}")
    abbreviation = factory.Sequence(lambda n: f"pc{n}")


class StationCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StationCategory

    workshop = factory.SubFactory(WorkshopFactory)
    name = factory.Sequence(lambda n: f"Station Category {n}")
    colour = factory.Sequence(lambda n: f"#{n % 0x1000000:06x}")


class MaterialCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MaterialCategory

    workshop = factory.SubFactory(WorkshopFactory)
    name = factory.Sequence(lambda n: f"Material Category {n}")


class ShiftDefinitionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ShiftDefinition

    workshop = factory.SubFactory(WorkshopFactory)
    name = factory.Sequence(lambda n: f"Shift {n}")
    start_time = datetime.time(8, 0)
    end_time = datetime.time(16, 0)
    days = factory.LazyFunction(lambda: ["mon", "tue", "wed", "thu", "fri"])


class WorkshopRoleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WorkshopRole
        skip_postgeneration_save = True

    workshop = factory.SubFactory(WorkshopFactory)
    name = factory.Sequence(lambda n: f"Role {n}")

    @factory.post_generation
    def default_clearances(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        self.default_clearances.add(*extracted)


class StationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Station
        skip_postgeneration_save = True

    workshop = factory.SubFactory(WorkshopFactory)
    name = factory.Sequence(lambda n: f"Station {n}")
    # Share the parent's workshop so the category is not in a foreign workshop.
    category = factory.SubFactory(
        StationCategoryFactory, workshop=factory.SelfAttribute("..workshop")
    )
    # code is left unset — Station.save() assigns the ST-NNN business id.

    @factory.post_generation
    def supported_operations(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        self.supported_operations.add(*extracted)


class MaterialFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Material

    workshop = factory.SubFactory(WorkshopFactory)
    name = factory.Sequence(lambda n: f"Oakboard {n}")
    category = factory.SubFactory(
        MaterialCategoryFactory, workshop=factory.SelfAttribute("..workshop")
    )
    unit = factory.SubFactory(
        UnitTypeFactory, workshop=factory.SelfAttribute("..workshop")
    )


class MaterialVariantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MaterialVariant

    material = factory.SubFactory(MaterialFactory)
    spec_label = factory.Sequence(lambda n: f"2000x150x{50 + n}")
    # Quantities are in the Material's unit (e.g. pieces). Default is a "clear"
    # variant: available (10 - 0) >= 0 and current_stock (10) >= min_threshold (5).
    current_stock = Decimal("10")
    reserved = Decimal("0")
    min_threshold = Decimal("5")
    lot_sizes = factory.LazyFunction(lambda: [2])


class ChangeRequestFactory(factory.django.DjangoModelFactory):
    """A valid pending user-target ChangeRequest (steady state).

    ``requested_by`` and ``assigned_to`` are scoped to the CR's own workshop via
    ``SelfAttribute("..workshop")`` (the StationFactory.category pattern), so the
    fixture is tenancy-consistent. ``target_id`` collapses onto the requester, as
    every MVP user-target CR is self-submitted. ``code`` is left unset —
    ``ChangeRequest.save()`` assigns the REQ-NNN business id. Resolved states are
    opt-in traits (``approved`` / ``rejected`` / ``cancelled``).
    """

    class Meta:
        model = ChangeRequest

    class Params:
        approved = factory.Trait(
            status=ChangeRequest.Status.APPROVED,
            resolution_note="Approved — change applied.",
            resolved_at=factory.LazyFunction(timezone.now),
        )
        rejected = factory.Trait(
            status=ChangeRequest.Status.REJECTED,
            resolution_note="Rejected — please raise this with your manager first.",
            resolved_at=factory.LazyFunction(timezone.now),
        )
        cancelled = factory.Trait(
            status=ChangeRequest.Status.CANCELLED,
            # Default cancellation cause; the deactivation cause is opt-in per test.
            cancel_reason=ChangeRequest.CancelReason.SUPERSEDED,
            resolved_at=factory.LazyFunction(timezone.now),
        )

    workshop = factory.SubFactory(WorkshopFactory)
    target_type = ChangeRequest.TargetType.USER
    # The self-submitting non-admin requester, in the CR's own workshop.
    requested_by = factory.SubFactory(
        UserFactory, workshop=factory.SelfAttribute("..workshop")
    )
    # The admin approver, in the same workshop.
    assigned_to = factory.SubFactory(
        UserFactory,
        workshop=factory.SelfAttribute("..workshop"),
        account_role=User.AccountRole.ADMIN,
    )
    # User-target CR collapses onto the requester (MVP): target_id == requested_by.id.
    target_id = factory.LazyAttribute(lambda o: o.requested_by.id)
    target_field = "first_name"
    current_value = "Alex"
    proposed_value = "Alexander"
    reason = "Legal name change after marriage."
    # status left to the model default (pending) — the steady state; traits override.
