from django.shortcuts import render, redirect
from django.contrib import messages
from django.views import View
from .models import Users


class LoginView(View):
    def get(self, request):
        if request.session.get("user_id"):
            return redirect("dashboard-upload")
        return render(request, "accounts/login.html")

    def post(self, request):
        email = request.POST.get("email", "").strip()
        pswd = request.POST.get("pswd", "")

        errors = {}

        if not email:
            errors["email"] = "Email is required."
        if not pswd:
            errors["pswd"] = "Password is required."

        if errors:
            return render(
                request,
                "accounts/login.html",
                {
                    "errors": errors,
                    "email": email,
                },
            )

        try:
            user = Users.objects.get(email=email)
        except Users.DoesNotExist:
            errors["email"] = "No account found with this email."
            return render(
                request,
                "accounts/login.html",
                {
                    "errors": errors,
                    "email": email,
                },
            )

        if user.pswd != pswd:
            errors["pswd"] = "Incorrect password."
            return render(
                request,
                "accounts/login.html",
                {
                    "errors": errors,
                    "email": email,
                },
            )

        # Login: store user info in session
        request.session["user_id"] = user.id
        request.session["user_name"] = f"{user.fname} {user.lname}"
        request.session["user_email"] = user.email

        messages.success(request, "Logged in successfully!")
        next_url = request.GET.get("next", "dashboard-home")
        return redirect(next_url)


class LogoutView(View):
    def get(self, request):
        request.session.flush()
        messages.info(request, "You have been logged out.")
        return redirect("account-login")
