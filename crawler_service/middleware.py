from django.conf import settings
from django.http import HttpResponse


LOCAL_DEV_ORIGINS = {'http://localhost:5173', 'http://127.0.0.1:5173'}


class LocalCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'OPTIONS':
            response = HttpResponse()
        else:
            response = self.get_response(request)

        origin = request.headers.get('Origin')

        allowed_origins = set(getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or []) | LOCAL_DEV_ORIGINS
        if origin in allowed_origins:
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Methods'] = ', '.join(
                getattr(settings, 'CORS_ALLOW_METHODS', ['GET', 'POST', 'OPTIONS'])
            )
            response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            response['Vary'] = 'Origin'

        return response
