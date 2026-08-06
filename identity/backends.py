from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import check_password, make_password
from django.db.models.functions import Lower

from .models import User

_DUMMY_PASSWORD = make_password("not-a-real-account-password")


class EmailBackend(BaseBackend):
    def authenticate(self, request, email=None, password=None, username=None, **kwargs):
        normalized = User.objects.normalize_email(email)
        user = None
        if normalized and username is None:
            user = (
                User.objects.annotate(login_email=Lower("email"))
                .filter(login_email=normalized)
                .first()
            )
        if user is None:
            check_password(password or "", _DUMMY_PASSWORD)
            return None
        if not user.is_active or not user.has_usable_password():
            check_password(password or "", _DUMMY_PASSWORD)
            return None
        return user if user.check_password(password or "") else None

    def get_user(self, user_id):
        try:
            user = User.objects.get(pk=user_id)
            return user if user.is_active else None
        except User.DoesNotExist:
            return None
