import hashlib
import logging
import secrets

from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .commands import (
    authenticate_user,
    correct_workshop_timezone,
    create_workshop,
    end_session,
    establish_session,
    invite_permanent_manager,
    register_administrator,
)
from .forms import (
    LoginForm,
    PermanentManagerInvitationForm,
    RegistrationForm,
    WorkshopCreationForm,
    WorkshopTimezoneCorrectionForm,
)
from .queries import (
    get_pending_manager_setup,
    get_timezone_correction_hint,
    resolve_authenticated_destination,
)
from .results import ResultCode

logger = logging.getLogger("identity")
GENERIC_REGISTRATION_ERROR = (
    "Registration is unavailable. Check your details and try again."
)
GENERIC_LOGIN_ERROR = "The email address or password was not recognised."


def _redirect_for(user):
    destination = resolve_authenticated_destination(user)
    return HttpResponseRedirect(destination.destination.value)


def _registration_form(data=None):
    if data is not None:
        return RegistrationForm(data)
    return RegistrationForm(initial={"submission_nonce": secrets.token_urlsafe(32)})


@require_http_methods(["GET", "POST"])
def register(request):
    if request.method == "GET":
        return render(request, "identity/register.html", {"form": _registration_form()})

    nonce = request.POST.get("submission_nonce", "")
    receipt_key = hashlib.sha256(nonce.encode()).hexdigest() if nonce else ""

    result = register_administrator(
        data=request.POST,
        remote_addr=request.META.get("REMOTE_ADDR"),
        idempotency_key=receipt_key,
    )
    if result.code == ResultCode.SUCCESS:
        session_result = establish_session(request, result.user)
        if session_result.succeeded:
            return _redirect_for(result.user)
        form = _registration_form()
        return render(
            request,
            "identity/register.html",
            {
                "form": form,
                "generic_error": "Your account was created. Sign in to continue.",
            },
            status=503,
        )
    if result.code == ResultCode.VALIDATION_ERROR:
        form = _registration_form(request.POST)
        form.is_valid()
    else:
        form = _registration_form()
    logger.info(
        "Administrator registration rejected",
        extra={"operation": "identity.registration", "result_code": "rejected"},
    )
    context = {"form": form}
    if result.code != ResultCode.VALIDATION_ERROR:
        context["generic_error"] = GENERIC_REGISTRATION_ERROR
    return render(request, "identity/register.html", context, status=400)


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.method == "GET":
        return render(request, "identity/login.html", {"form": LoginForm()})
    form = LoginForm(request.POST)
    if form.is_valid():
        result = authenticate_user(request, **form.cleaned_data)
        if result.succeeded:
            session_result = establish_session(request, result.user)
            if session_result.succeeded:
                return _redirect_for(result.user)
            form = LoginForm()
            return render(
                request,
                "identity/login.html",
                {
                    "form": form,
                    "generic_error": "Sign in is temporarily unavailable. Try again.",
                },
                status=503,
            )
    form = LoginForm()
    logger.info(
        "Login rejected",
        extra={"operation": "identity.login", "result_code": "rejected"},
    )
    return render(
        request,
        "identity/login.html",
        {"form": form, "generic_error": GENERIC_LOGIN_ERROR},
        status=400,
    )


@require_POST
def logout_view(request):
    end_session(request)
    return redirect("login")


def root_destination(request):
    return _redirect_for(request.user)


def _workshop_form(user, data=None):
    if data is not None:
        return WorkshopCreationForm(data)
    return WorkshopCreationForm(
        initial={
            "submission_nonce": secrets.token_urlsafe(32),
            "expected_user_version": user.version,
            "contact_email": user.email,
        }
    )


@require_http_methods(["GET", "POST"])
def workshop_onboarding(request):
    resolution = resolve_authenticated_destination(request.user)
    if not resolution.supported or resolution.destination.value != request.path:
        return _redirect_for(request.user)
    user = resolution.user
    if request.method == "GET":
        return render(
            request,
            "onboarding/create_workshop.html",
            {"form": _workshop_form(user), "identity_user": user},
        )

    nonce = request.POST.get("submission_nonce", "")
    receipt_key = hashlib.sha256(nonce.encode()).hexdigest() if nonce else ""
    result = create_workshop(
        actor_id=user.id, data=request.POST, idempotency_key=receipt_key
    )
    if result.code in {ResultCode.SUCCESS, ResultCode.REPLAY}:
        request.session["workshop_saved"] = True
        return redirect("onboarding-manager")
    if result.code == ResultCode.ALREADY_ADVANCED:
        return _redirect_for(user)
    if result.code == ResultCode.VALIDATION_ERROR:
        form = _workshop_form(user, request.POST)
        form.is_valid()
        status = 400
        generic_error = None
    else:
        form = _workshop_form(user)
        status = 503
        generic_error = "Workshop setup is temporarily unavailable. Try again."
    logger.info(
        "Workshop creation rejected",
        extra={"operation": "identity.workshop.create", "result_code": "rejected"},
    )
    return render(
        request,
        "onboarding/create_workshop.html",
        {
            "form": form,
            "identity_user": user,
            "generic_error": generic_error,
        },
        status=status,
    )


def _manager_form(user, data=None):
    if data is not None:
        return PermanentManagerInvitationForm(data)
    return PermanentManagerInvitationForm(
        initial={
            "submission_nonce": secrets.token_urlsafe(32),
            "expected_workshop_version": user.workshop.version,
        }
    )


def _timezone_form(user, data=None):
    if data is not None:
        return WorkshopTimezoneCorrectionForm(data)
    return WorkshopTimezoneCorrectionForm(
        initial={
            "timezone_action": "correct",
            "submission_nonce": secrets.token_urlsafe(32),
            "expected_workshop_version": user.workshop.version,
        }
    )


def _timezone_context(user, data=None, status_message=None):
    return {
        "timezone_hint": get_timezone_correction_hint(user),
        "timezone_form": _timezone_form(user, data),
        "timezone_status": status_message,
    }


def _handle_timezone_post(request, user):
    nonce = request.POST.get("submission_nonce", "")
    command_key = hashlib.sha256(nonce.encode()).hexdigest() if nonce else ""
    result = correct_workshop_timezone(
        actor_id=user.id, data=request.POST, idempotency_key=command_key
    )
    if result.code in {ResultCode.SUCCESS, ResultCode.REPLAY}:
        request.session["timezone_status"] = "Workshop timezone corrected."
        return redirect(request.path)
    if result.code == ResultCode.VALIDATION_ERROR:
        return None, _timezone_context(user, request.POST), 400
    messages = {
        ResultCode.STALE: "Workshop setup changed. Review the current timezone.",
        ResultCode.ALREADY_ADVANCED: "Timezone correction is no longer available.",
    }
    request.session["timezone_status"] = messages.get(
        result.code, "Timezone correction is unavailable."
    )
    return redirect(request.path)


@require_http_methods(["GET", "POST"])
def onboarding_manager(request):
    resolution = resolve_authenticated_destination(request.user)
    if not resolution.supported or resolution.destination.value != request.path:
        return _redirect_for(request.user)
    user = resolution.user
    if request.method == "POST" and request.POST.get("timezone_action") == "correct":
        handled = _handle_timezone_post(request, user)
        if not isinstance(handled, tuple):
            return handled
        _, timezone_context, status = handled
        context = {
            "identity_user": user,
            "form": _manager_form(user),
            **timezone_context,
        }
        return render(request, "onboarding/invite_manager.html", context, status=status)
    if request.method == "GET":
        timezone_status = request.session.pop("timezone_status", None)
        return render(
            request,
            "onboarding/invite_manager.html",
            {
                "identity_user": user,
                "form": _manager_form(user),
                "workshop_saved": bool(request.session.pop("workshop_saved", False)),
                **_timezone_context(user, status_message=timezone_status),
            },
        )
    nonce = request.POST.get("submission_nonce", "")
    receipt_key = hashlib.sha256(nonce.encode()).hexdigest() if nonce else ""
    result = invite_permanent_manager(
        actor_id=user.id, data=request.POST, idempotency_key=receipt_key
    )
    if result.code in {ResultCode.SUCCESS, ResultCode.REPLAY}:
        return redirect("onboarding-cockpit")
    if result.code == ResultCode.ALREADY_ADVANCED:
        return _redirect_for(user)
    if result.code == ResultCode.VALIDATION_ERROR:
        form = _manager_form(user, request.POST)
        form.is_valid()
        status = 400
        generic_error = None
    else:
        form = _manager_form(user)
        status = 503
        generic_error = "Manager invitation is temporarily unavailable. Try again."
    logger.info(
        "Manager invitation rejected",
        extra={"operation": "identity.manager.invite", "result_code": "rejected"},
    )
    return render(
        request,
        "onboarding/invite_manager.html",
        {
            "identity_user": user,
            "form": form,
            "generic_error": generic_error,
            **_timezone_context(user),
        },
        status=status,
    )


@require_http_methods(["GET", "POST"])
def onboarding_cockpit(request):
    resolution = resolve_authenticated_destination(request.user)
    if not resolution.supported or resolution.destination.value != request.path:
        return _redirect_for(request.user)
    setup = get_pending_manager_setup(resolution.user)
    if setup is None:
        logger.error(
            "Pending manager projection unavailable",
            extra={
                "operation": "identity.manager.pending.read",
                "result_code": "failed",
                "workshop_id": resolution.user.workshop_id,
            },
        )
        return redirect("login")
    if request.method == "POST":
        if request.POST.get("timezone_action") != "correct":
            return redirect(request.path)
        handled = _handle_timezone_post(request, resolution.user)
        if not isinstance(handled, tuple):
            return handled
        _, timezone_context, status = handled
        return render(
            request,
            "onboarding/setup_cockpit.html",
            {
                "identity_user": resolution.user,
                "setup": setup,
                **timezone_context,
            },
            status=status,
        )
    timezone_status = request.session.pop("timezone_status", None)
    return render(
        request,
        "onboarding/setup_cockpit.html",
        {
            "identity_user": resolution.user,
            "setup": setup,
            **_timezone_context(resolution.user, status_message=timezone_status),
        },
    )


def holding(request):
    resolution = resolve_authenticated_destination(request.user)
    if not resolution.supported or resolution.destination.value != request.path:
        return _redirect_for(request.user)
    return render(
        request,
        "onboarding/holding.html",
        {"identity_user": resolution.user},
    )


def dashboard(request):
    resolution = resolve_authenticated_destination(request.user)
    if not resolution.supported or resolution.destination.value != request.path:
        return _redirect_for(request.user)
    return render(
        request,
        "onboarding/stage_handoff.html",
        {
            "identity_user": resolution.user,
            "stage": "operational",
            "role_home": resolution.role_home,
        },
    )
