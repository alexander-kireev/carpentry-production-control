from django.urls import path

from .views import (
    dashboard,
    holding,
    login_view,
    logout_view,
    onboarding_cockpit,
    onboarding_manager,
    register,
    workshop_onboarding,
)

urlpatterns = [
    path("register", register, name="register"),
    path("login", login_view, name="login"),
    path("logout", logout_view, name="logout"),
    path("onboarding/workshop", workshop_onboarding, name="onboarding-workshop"),
    path("onboarding/manager", onboarding_manager, name="onboarding-manager"),
    path("onboarding", onboarding_cockpit, name="onboarding-cockpit"),
    path("onboarding/holding", holding, name="onboarding-holding"),
    path("dashboard", dashboard, name="dashboard"),
]
