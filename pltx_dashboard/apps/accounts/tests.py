from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Feature, Role, Users


class AuthAndRoutingSmokeTests(TestCase):
    def setUp(self):
        self.main_user = Users.objects.create(
            fname="Main",
            lname="User",
            email="main@example.com",
            pswd="secret",
            cpswd="secret",
        )

    def _set_session_user(self, user_id):
        session = self.client.session
        session["user_id"] = user_id
        session.save()

    def test_login_page_loads(self):
        response = self.client.get(reverse("account-login"))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_home_redirects_to_login_without_session(self):
        response = self.client.get(reverse("dashboard-home"))
        self.assertRedirects(response, reverse("account-login"))

    def test_dashboard_home_redirects_main_user_to_business_dashboard(self):
        self._set_session_user(self.main_user.id)
        response = self.client.get(reverse("dashboard-home"))
        self.assertRedirects(response, reverse("business-dashboard"))

    def test_feature_guard_redirects_sub_user_without_feature(self):
        upload_feature = Feature.objects.create(name="Upload", code_name="upload_data")
        role = Role.objects.create(name="Upload role", created_by=self.main_user)
        role.features.add(upload_feature)
        sub_user = Users.objects.create(
            fname="Sub",
            lname="User",
            email="sub@example.com",
            pswd="secret",
            cpswd="secret",
            created_by=self.main_user,
            role=role,
        )

        self._set_session_user(sub_user.id)
        response = self.client.get(reverse("business-dashboard"))
        self.assertRedirects(response, reverse("dashboard-upload"))
