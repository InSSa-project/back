from django.conf import settings
from django.http import HttpResponse


class LocalCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'OPTIONS':
            response = HttpResponse()
        else:
            response = self.get_response(request)

        origin = request.headers.get('Origin')

        if origin in getattr(settings, 'CORS_ALLOWED_ORIGINS', []):
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            response['Vary'] = 'Origin'

        return response
