from django.http import JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.exceptions import AuthenticationFailed

from apps.users.authentication import JwtAuthentication

from .services import build_home_dashboard


@require_GET
def home_dashboard(request):
    user = _authenticate_optional_user(request)
    if user is None:
        return JsonResponse({'detail': 'Authentication credentials were invalid.'}, status=401)
    return JsonResponse(build_home_dashboard(user))


def _authenticate_optional_user(request):
    if getattr(request.user, 'is_authenticated', False):
        return request.user
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header:
        return request.user
    try:
        authenticated = JwtAuthentication().authenticate(request)
    except AuthenticationFailed:
        return None
    if authenticated is None:
        return request.user
    user, _auth = authenticated
    request.user = user
    return user

