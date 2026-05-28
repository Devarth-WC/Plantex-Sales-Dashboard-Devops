from apps.accounts.models import Users


def get_logged_in_user(request):
    """Get the logged-in Users instance from session, or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        return Users.objects.get(id=user_id)
    except Users.DoesNotExist:
        return None
