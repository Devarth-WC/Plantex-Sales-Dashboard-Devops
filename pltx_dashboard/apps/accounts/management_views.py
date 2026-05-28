from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views import View
from .models import Users, Role, Feature
from .decorators import require_feature


class UserManagementView(View):
    @require_feature("user_management")
    def get(self, request):
        data_owner = (
            request.user.created_by if request.user.created_by else request.user
        )
        sub_users = Users.objects.filter(created_by=data_owner)
        roles = Role.objects.filter(created_by=data_owner)
        if request.user.is_main_user:
            user_features = [f.code_name for f in Feature.objects.all()]
        else:
            user_features = (
                [f.code_name for f in request.user.role.features.all()]
                if request.user.role
                else []
            )

        return render(
            request,
            "accounts/user_management.html",
            {
                "sub_users": sub_users,
                "roles": roles,
                "logged_user": request.user,
                "user_features": user_features,
                "payload_json": "null",
                "selected_filters_json": "{}",
            },
        )

    @require_feature("user_management")
    def post(self, request):
        action = request.POST.get("action")
        data_owner = (
            request.user.created_by if request.user.created_by else request.user
        )

        if action == "create":
            fname = request.POST.get("fname", "").strip()
            lname = request.POST.get("lname", "").strip()
            email = request.POST.get("email", "").strip()
            pswd = request.POST.get("pswd", "")
            role_id = request.POST.get("role_id")

            if Users.objects.filter(email=email).exists():
                messages.error(request, "Email already exists.")
            else:
                role = (
                    Role.objects.filter(id=role_id, created_by=data_owner).first()
                    if role_id
                    else None
                )
                Users.objects.create(
                    fname=fname,
                    lname=lname,
                    email=email,
                    pswd=pswd,
                    cpswd=pswd,
                    created_by=data_owner,
                    role=role,
                )
                messages.success(request, "User created successfully.")

        elif action == "update":
            user_id = request.POST.get("user_id")
            user_obj = get_object_or_404(Users, id=user_id, created_by=data_owner)

            user_obj.fname = request.POST.get("fname", "").strip()
            user_obj.lname = request.POST.get("lname", "").strip()
            role_id = request.POST.get("role_id")
            user_obj.role = (
                Role.objects.filter(id=role_id, created_by=data_owner).first()
                if role_id
                else None
            )

            new_pswd = request.POST.get("pswd", "")
            if new_pswd:
                user_obj.pswd = new_pswd
                user_obj.cpswd = new_pswd

            user_obj.save()
            messages.success(request, "User updated successfully.")

        elif action == "delete":
            user_id = request.POST.get("user_id")
            user_obj = get_object_or_404(Users, id=user_id, created_by=data_owner)
            user_obj.delete()
            messages.success(request, "User deleted successfully.")

        return redirect("user-management")


class RoleManagementView(View):
    @require_feature("role_management")
    def get(self, request):
        data_owner = (
            request.user.created_by if request.user.created_by else request.user
        )
        roles = Role.objects.filter(created_by=data_owner)
        if request.user.is_main_user:
            user_features = [f.code_name for f in Feature.objects.all()]
        else:
            user_features = (
                [f.code_name for f in request.user.role.features.all()]
                if request.user.role
                else []
            )

        features = Feature.objects.all()
        return render(
            request,
            "accounts/role_management.html",
            {
                "roles": roles,
                "features": features,
                "logged_user": request.user,
                "user_features": user_features,
                "payload_json": "null",
                "selected_filters_json": "{}",
            },
        )

    @require_feature("role_management")
    def post(self, request):
        action = request.POST.get("action")
        data_owner = (
            request.user.created_by if request.user.created_by else request.user
        )

        if action == "create":
            name = request.POST.get("name", "").strip()
            feature_ids = request.POST.getlist("features")

            role = Role.objects.create(name=name, created_by=data_owner)
            if feature_ids:
                role.features.set(Feature.objects.filter(id__in=feature_ids))
            messages.success(request, "Role created successfully.")

        elif action == "update":
            role_id = request.POST.get("role_id")
            role = get_object_or_404(Role, id=role_id, created_by=data_owner)
            role.name = request.POST.get("name", "").strip()

            feature_ids = request.POST.getlist("features")
            role.features.set(Feature.objects.filter(id__in=feature_ids))
            role.save()
            messages.success(request, "Role updated successfully.")

        elif action == "delete":
            role_id = request.POST.get("role_id")
            role = get_object_or_404(Role, id=role_id, created_by=data_owner)
            role.delete()
            messages.success(request, "Role deleted successfully.")

        return redirect("role-management")
