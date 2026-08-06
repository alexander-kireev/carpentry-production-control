import logging
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.utils import timezone

from workshops.models import OperationType, Workshop
from workshops.protected_configuration import (
    ProtectedConfigurationError,
    resolve_admin_role,
    resolve_protected_configuration,
    verify_workshop_protected_pair,
)

from .delivery import schedule_invitation_delivery
from .forms import (
    PermanentManagerInvitationForm,
    RegistrationForm,
    WorkshopCreationForm,
)
from .models import (
    EmailDeliveryIntent,
    ManagerInvitationCommandReceipt,
    RegistrationCommandReceipt,
    User,
    UserInvitation,
    WorkshopCreationCommandReceipt,
)
from .results import CommandResult, ResultCode
from .security import (
    check_activation_code,
    generate_invitation_token,
    manager_payload_fingerprint,
    registration_payload_fingerprint,
    workshop_payload_fingerprint,
)

logger = logging.getLogger("identity")
BACKEND = "identity.backends.EmailBackend"


def register_administrator(*, data, remote_addr, idempotency_key):
    if not check_activation_code(data.get("activation_code"), remote_addr):
        return CommandResult(ResultCode.REGISTRATION_UNAVAILABLE)

    form = RegistrationForm(data)
    if not form.is_valid():
        return CommandResult(
            ResultCode.VALIDATION_ERROR, errors=form.errors.get_json_data()
        )

    values = form.cleaned_data
    fingerprint = registration_payload_fingerprint(
        first_name=values["first_name"],
        last_name=values["last_name"],
        date_of_birth=values["date_of_birth"],
        email=values["email"],
    )
    if not idempotency_key:
        return CommandResult(ResultCode.REGISTRATION_UNAVAILABLE)

    try:
        with transaction.atomic():
            receipt = (
                RegistrationCommandReceipt.objects.select_related("result_user")
                .filter(idempotency_key=idempotency_key)
                .first()
            )
            if receipt is not None:
                if (
                    receipt.fingerprint_version == 1
                    and bytes(receipt.payload_fingerprint) == fingerprint
                    and receipt.result_user.check_password(values["password"])
                ):
                    return CommandResult(ResultCode.SUCCESS, user=receipt.result_user)
                return CommandResult(ResultCode.REGISTRATION_UNAVAILABLE)

            user = User.objects.create_user(
                email=values["email"],
                password=values["password"],
                first_name=values["first_name"],
                last_name=values["last_name"],
                date_of_birth=values["date_of_birth"],
                account_role=User.AccountRole.ADMIN,
                status=User.Status.ACTIVE,
                onboarding_state=User.OnboardingState.REGISTERED_NO_WORKSHOP,
                workshop=None,
                workshop_role=None,
                version=1,
            )
            RegistrationCommandReceipt.objects.create(
                idempotency_key=idempotency_key,
                fingerprint_version=1,
                payload_fingerprint=fingerprint,
                result_user=user,
            )
            return CommandResult(ResultCode.SUCCESS, user=user)
    except IntegrityError:
        receipt = (
            RegistrationCommandReceipt.objects.select_related("result_user")
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if (
            receipt is not None
            and receipt.fingerprint_version == 1
            and bytes(receipt.payload_fingerprint) == fingerprint
            and receipt.result_user.check_password(values["password"])
        ):
            return CommandResult(ResultCode.SUCCESS, user=receipt.result_user)
        return CommandResult(ResultCode.REGISTRATION_UNAVAILABLE)


def authenticate_user(request, *, email, password):
    user = authenticate(request, email=email, password=password)
    if user is None:
        return CommandResult(ResultCode.AUTHENTICATION_FAILED)
    return CommandResult(ResultCode.SUCCESS, user=user)


def establish_session(request, user):
    try:
        login(request, user, backend=BACKEND)
        request.session.save()
        return CommandResult(ResultCode.SUCCESS, user=user)
    except Exception:
        engine = import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore()
        request.session.modified = False
        request.session.accessed = False
        request.user = AnonymousUser()
        logger.error(
            "Identity session establishment failed",
            extra={"operation": "identity.session.establish", "result_code": "failed"},
        )
        return CommandResult(ResultCode.SESSION_FAILED, user=user)


def end_session(request):
    logout(request)
    return CommandResult(ResultCode.SUCCESS)


def _workshop_result_is_exact(receipt, user, admin_role):
    workshop = receipt.result_workshop
    if not (
        receipt.actor_user_id == user.id
        and user.workshop_id == workshop.id
        and user.workshop_role_id == admin_role.id
        and user.account_role == User.AccountRole.ADMIN
        and user.status == User.Status.ACTIVE
        and user.onboarding_state is None
        and workshop.status == Workshop.Status.MANAGER_REQUIRED
    ):
        return False
    try:
        verify_workshop_protected_pair(workshop)
    except ProtectedConfigurationError:
        return False
    return True


def create_workshop(*, actor_id, data, idempotency_key):
    form = WorkshopCreationForm(data)
    if not form.is_valid():
        return CommandResult(
            ResultCode.VALIDATION_ERROR, errors=form.errors.get_json_data()
        )
    if not idempotency_key:
        return CommandResult(ResultCode.WORKSHOP_UNAVAILABLE)
    return _create_workshop_validated(
        actor_id=actor_id, form=form, idempotency_key=idempotency_key
    )


def _manager_receipt_is_exact(receipt, *, workshop, actor, fingerprint):
    candidate = receipt.candidate_user
    invitation = receipt.result_invitation
    intent = invitation.delivery_intents.filter(invitation_generation=1).first()
    return (
        receipt.actor_user_id == actor.id
        and receipt.workshop_id == workshop.id
        and receipt.fingerprint_version == 1
        and bytes(receipt.payload_fingerprint) == fingerprint
        and workshop.status == Workshop.Status.MANAGER_ACTIVATION_PENDING
        and candidate.workshop_id == workshop.id
        and candidate.account_role == User.AccountRole.MANAGER
        and candidate.status == User.Status.PENDING
        and candidate.onboarding_state is None
        and candidate.workshop_role is not None
        and candidate.workshop_role.machine_key == "undefined"
        and candidate.workshop_role.workshop_id is None
        and invitation.user_id == candidate.id
        and invitation.workshop_id == workshop.id
        and invitation.status == UserInvitation.Status.PENDING
        and invitation.invitation_generation == 1
        and intent is not None
        and intent.status
        in {
            EmailDeliveryIntent.Status.PENDING,
            EmailDeliveryIntent.Status.SENT,
            EmailDeliveryIntent.Status.FAILED,
        }
    )


def _recover_manager_invitation(*, actor_id, idempotency_key, fingerprint):
    receipt = (
        ManagerInvitationCommandReceipt.objects.select_related(
            "workshop",
            "actor_user",
            "candidate_user__workshop_role",
            "result_invitation",
        )
        .filter(actor_user_id=actor_id, idempotency_key=idempotency_key)
        .first()
    )
    if receipt is None:
        return None
    if _manager_receipt_is_exact(
        receipt,
        workshop=receipt.workshop,
        actor=receipt.actor_user,
        fingerprint=fingerprint,
    ):
        return CommandResult(
            ResultCode.REPLAY,
            user=receipt.actor_user,
            workshop=receipt.workshop,
            candidate=receipt.candidate_user,
            invitation=receipt.result_invitation,
        )
    return CommandResult(ResultCode.INVITATION_UNAVAILABLE)


def invite_permanent_manager(*, actor_id, data, idempotency_key):
    form = PermanentManagerInvitationForm(data)
    if not form.is_valid():
        return CommandResult(
            ResultCode.VALIDATION_ERROR, errors=form.errors.get_json_data()
        )
    if not idempotency_key:
        return CommandResult(ResultCode.INVITATION_UNAVAILABLE)
    return _invite_permanent_manager_validated(
        actor_id=actor_id, form=form, idempotency_key=idempotency_key
    )


def _invite_permanent_manager_validated(*, actor_id, form, idempotency_key):
    values = form.cleaned_data
    fingerprint = manager_payload_fingerprint(
        first_name=values["first_name"],
        last_name=values["last_name"],
        date_of_birth=values["date_of_birth"],
        email=values["email"],
    )
    discovered = User.objects.filter(pk=actor_id).values("workshop_id").first()
    if discovered is None or discovered["workshop_id"] is None:
        return CommandResult(ResultCode.INVITATION_UNAVAILABLE)
    try:
        with transaction.atomic():
            workshop = Workshop.objects.select_for_update().get(
                pk=discovered["workshop_id"]
            )
            actor = User.objects.select_for_update().get(pk=actor_id)
            protected = resolve_protected_configuration()
            receipt = (
                ManagerInvitationCommandReceipt.objects.select_related(
                    "candidate_user__workshop_role", "result_invitation"
                )
                .filter(workshop=workshop, idempotency_key=idempotency_key)
                .first()
            )
            if receipt is not None:
                if _manager_receipt_is_exact(
                    receipt, workshop=workshop, actor=actor, fingerprint=fingerprint
                ):
                    return CommandResult(
                        ResultCode.REPLAY,
                        user=actor,
                        workshop=workshop,
                        candidate=receipt.candidate_user,
                        invitation=receipt.result_invitation,
                    )
                return CommandResult(ResultCode.INVITATION_UNAVAILABLE)
            exact_admin = (
                actor.workshop_id == workshop.id
                and actor.workshop_role_id == protected.admin_role.id
                and actor.account_role == User.AccountRole.ADMIN
                and actor.status == User.Status.ACTIVE
                and actor.onboarding_state is None
            )
            if not exact_admin or workshop.status != Workshop.Status.MANAGER_REQUIRED:
                return CommandResult(ResultCode.ALREADY_ADVANCED, user=actor)
            if workshop.version != values["expected_workshop_version"]:
                return CommandResult(ResultCode.STALE, user=actor)
            if (
                values["email"] == actor.email.casefold()
                or User.objects.filter(email__iexact=values["email"]).exists()
            ):
                return CommandResult(ResultCode.INVITATION_UNAVAILABLE)
            if User.objects.filter(
                workshop=workshop, account_role=User.AccountRole.MANAGER
            ).exists():
                return CommandResult(ResultCode.INVITATION_UNAVAILABLE)

            candidate = User(
                email=values["email"],
                first_name=values["first_name"],
                last_name=values["last_name"],
                date_of_birth=values["date_of_birth"],
                account_role=User.AccountRole.MANAGER,
                status=User.Status.PENDING,
                onboarding_state=None,
                workshop=workshop,
                workshop_role=protected.undefined_role,
                version=1,
            )
            candidate.set_unusable_password()
            candidate.save()
            raw_token, token_salt, token_digest = generate_invitation_token()
            issued_at = timezone.now()
            invitation = UserInvitation.objects.create(
                user=candidate,
                workshop=workshop,
                token_hash=token_digest,
                token_hash_version=1,
                token_salt=token_salt,
                invitation_generation=1,
                status=UserInvitation.Status.PENDING,
                issued_at=issued_at,
                expires_at=issued_at + timedelta(hours=72),
            )
            intent = EmailDeliveryIntent.objects.create(
                invitation=invitation,
                purpose="invitation",
                recipient_email=candidate.email,
                invitation_generation=1,
            )
            ManagerInvitationCommandReceipt.objects.create(
                workshop=workshop,
                actor_user=actor,
                candidate_user=candidate,
                result_invitation=invitation,
                idempotency_key=idempotency_key,
                fingerprint_version=1,
                payload_fingerprint=fingerprint,
            )
            workshop.status = Workshop.Status.MANAGER_ACTIVATION_PENDING
            workshop.version += 1
            workshop.save(update_fields=("status", "version"))
            transaction.on_commit(
                lambda: schedule_invitation_delivery(
                    intent_id=intent.id,
                    invitation_id=invitation.id,
                    generation=1,
                    raw_token=raw_token,
                )
            )
            return CommandResult(
                ResultCode.SUCCESS,
                user=actor,
                workshop=workshop,
                candidate=candidate,
                invitation=invitation,
                delivery_intent=intent,
            )
    except IntegrityError, ProtectedConfigurationError, User.DoesNotExist:
        recovered = _recover_manager_invitation(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if recovered is not None:
            return recovered
        logger.error(
            "Manager invitation unavailable",
            extra={
                "operation": "identity.manager.invite",
                "result_code": "failed",
            },
        )
        return CommandResult(ResultCode.INVITATION_UNAVAILABLE)


def _create_workshop_validated(*, actor_id, form, idempotency_key):
    values = form.cleaned_data
    fingerprint = workshop_payload_fingerprint(
        name=values["name"],
        address=values["address"],
        email=values["contact_email"],
        timezone=values["timezone"],
        expected_version=values["expected_user_version"],
    )
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=actor_id)
            admin_role = resolve_admin_role()
            receipt = (
                WorkshopCreationCommandReceipt.objects.select_related("result_workshop")
                .filter(idempotency_key=idempotency_key)
                .first()
            )
            if receipt is not None:
                exact_fingerprint = (
                    receipt.fingerprint_version == 1
                    and bytes(receipt.payload_fingerprint) == fingerprint
                )
                if exact_fingerprint and _workshop_result_is_exact(
                    receipt, user, admin_role
                ):
                    return CommandResult(
                        ResultCode.REPLAY, user=user, workshop=receipt.result_workshop
                    )
                return CommandResult(ResultCode.WORKSHOP_UNAVAILABLE)

            if user.workshop_id is not None:
                return CommandResult(ResultCode.ALREADY_ADVANCED, user=user)
            if user.version != values["expected_user_version"]:
                return CommandResult(ResultCode.STALE, user=user)
            if not (
                user.status == User.Status.ACTIVE
                and user.account_role == User.AccountRole.ADMIN
                and user.onboarding_state == User.OnboardingState.REGISTERED_NO_WORKSHOP
                and user.workshop_role_id is None
            ):
                return CommandResult(ResultCode.WORKSHOP_UNAVAILABLE)

            workshop = Workshop.objects.create(
                name=values["name"],
                address=values["address"],
                email=values["contact_email"],
                timezone=values["timezone"],
                status=Workshop.Status.MANAGER_REQUIRED,
                version=1,
                station_code_counter=0,
                customer_code_counter=0,
                order_code_counter=0,
                build_code_counter=0,
            )
            OperationType.objects.bulk_create(
                [
                    OperationType(
                        workshop=workshop,
                        name="Build Planning",
                        machine_key="build_planning",
                        is_production=False,
                        requires_clearance=True,
                        status=OperationType.Status.ACTIVE,
                        version=1,
                    ),
                    OperationType(
                        workshop=workshop,
                        name="Station Maintenance",
                        machine_key="station_maintenance",
                        is_production=False,
                        requires_clearance=True,
                        status=OperationType.Status.ACTIVE,
                        version=1,
                    ),
                ]
            )
            user.workshop = workshop
            user.workshop_role = admin_role
            user.onboarding_state = None
            user.version += 1
            user.save(
                update_fields=(
                    "workshop",
                    "workshop_role",
                    "onboarding_state",
                    "version",
                    "email",
                )
            )
            WorkshopCreationCommandReceipt.objects.create(
                idempotency_key=idempotency_key,
                fingerprint_version=1,
                payload_fingerprint=fingerprint,
                actor_user=user,
                result_workshop=workshop,
            )
            return CommandResult(ResultCode.SUCCESS, user=user, workshop=workshop)
    except IntegrityError, ProtectedConfigurationError, User.DoesNotExist:
        logger.error(
            "Workshop creation unavailable",
            extra={"operation": "identity.workshop.create", "result_code": "failed"},
        )
        return CommandResult(ResultCode.WORKSHOP_UNAVAILABLE)
