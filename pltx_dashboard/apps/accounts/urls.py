from django.urls import path
from .views import LoginView, LogoutView
from .management_views import UserManagementView, RoleManagementView

urlpatterns = [
    path("login/", LoginView.as_view(), name="account-login"),
    path("logout/", LogoutView.as_view(), name="account-logout"),
    path("users/", UserManagementView.as_view(), name="user-management"),
    path("roles/", RoleManagementView.as_view(), name="role-management"),
]
