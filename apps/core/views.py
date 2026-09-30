from django.http import JsonResponse


def home(request):
    return JsonResponse({"status": "ok", "service": "built-to-work"})


def health(request):
    return JsonResponse({"status": "ok"})