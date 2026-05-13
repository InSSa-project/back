from rest_framework.response import Response


def success_response(data=None, message='OK', status_code=200):
    return Response(
        {
            'status': 'success',
            'data': data if data is not None else {},
            'message': message,
        },
        status=status_code,
    )


def error_response(message, code=400, status_code=400):
    return Response(
        {
            'status': 'error',
            'message': message,
            'code': code,
        },
        status=status_code,
    )
