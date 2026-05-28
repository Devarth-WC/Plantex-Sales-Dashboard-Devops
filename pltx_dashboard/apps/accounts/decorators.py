from functools import wraps
from django.shortcuts import redirect
from .models import Users


def _first_allowed_dashboard_for(user):
    """Return the name of the first dashboard route the user can access.

    Priority order: category -> ceo -> business -> upload -> replenishment.
    Falls back to 'account-login' if nothing available.
    """
    if not user:
        return "account-login"
    if user.is_main_user:
        return "business-dashboard"
    if not user.role:
        return "account-login"
    feature_codes = set(f.code_name for f in user.role.features.all())
    if "category_dashboard" in feature_codes:
        return "category-dashboard"
    if "ceo_dashboard" in feature_codes:
        return "ceo-dashboard"
    if "business_dashboard" in feature_codes:
        return "business-dashboard"
    if "upload_data" in feature_codes:
        return "dashboard-upload"
    if "replenishment" in feature_codes:
        return "replenishment-index"
    return "account-login"


def require_feature(feature_code):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(arg, *args, **kwargs):
            # arg is request for FBV, self for CBV method
            request = arg if hasattr(arg, "session") else args[0]
            user_id = request.session.get("user_id")
            if not user_id:
                return redirect("account-login")

            try:
                user = Users.objects.get(id=user_id)
            except Users.DoesNotExist:
                return redirect("account-login")

            # attach for convenience
            request.user = user

            # main users have all features
            if user.is_main_user:
                return view_func(arg, *args, **kwargs)

            # Sub-user logic
            if not user.role:
                return redirect(_first_allowed_dashboard_for(user))

            has_feature = user.role.features.filter(code_name=feature_code).exists()
            if not has_feature:
                # Redirect to the first dashboard the user does have access to
                return redirect(_first_allowed_dashboard_for(user))

            return view_func(arg, *args, **kwargs)

        return _wrapped_view

    return decorator
