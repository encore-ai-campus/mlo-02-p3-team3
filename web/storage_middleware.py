"""Storage errors fail closed without exposing database connection details."""
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from pymongo.errors import PyMongoError

def unavailable():
    return JsonResponse({'error': '저장소를 확인할 수 없어요. 잠시 후 다시 시도해 주세요.', 'code': 'STORAGE_UNAVAILABLE'}, status=503)

class MongoSessionMiddleware(SessionMiddleware):
    def process_response(self, request, response):
        if response.status_code >= 500:
            return response
        try:
            return super().process_response(request, response)
        except PyMongoError:
            return unavailable()

class StorageFailureMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        from .services import FlowError
        if isinstance(exception, PyMongoError) or (isinstance(exception, FlowError) and exception.status == 503):
            return unavailable()
        return None
