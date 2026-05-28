from rest_framework.authentication import BaseAuthentication

from apps.accounts.models import Users


class SessionUserIdAuthentication(BaseAuthentication):
    """
    Lightweight DRF auth bridge for this project:
    use Django session `user_id` and return `apps.accounts.Users`.
    """

    def authenticate(self, request):
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        try:
            user = Users.objects.get(id=user_id)
        except Users.DoesNotExist:
            return None
        return (user, None)
