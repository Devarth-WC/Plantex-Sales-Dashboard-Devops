from django.contrib import admin
from .models import Users, Feature, Role


@admin.register(Users)
class UsersAdmin(admin.ModelAdmin):
    list_display = ("fname", "lname", "email", "role", "is_main_user")
    list_filter = ("role",)
    search_fields = ("fname", "lname", "email")


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "code_name")
    search_fields = ("name", "code_name")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
