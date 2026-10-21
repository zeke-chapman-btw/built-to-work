"""Staff-only Notifications feed and explicit CSRF-protected read actions."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from .views import staff_view
from . import notifications,due_dates

@staff_view
def updates(request):
    if request.method!='GET':return HttpResponseNotAllowed(['GET'])
    page=Paginator(notifications.visible_notifications(request.user),20).get_page(request.GET.get('page'))
    return render(request,'operations/updates.html',{'page_title':'Notifications','notification_page':page,'due_attention':due_dates.due_attention(request.user)})

def destination(request):
    if request.POST.get('return_to')=='home':return reverse('internal:home')
    raw=request.POST.get('page','1')
    page=int(raw) if raw.isascii() and raw.isdecimal() and len(raw)<7 else 1
    return reverse('operations:updates')+('?page='+str(page) if page>1 else '')

@staff_view
def read(request,notification_id):
    if request.method!='POST':return HttpResponseNotAllowed(['POST'])
    item=get_object_or_404(notifications.visible_notifications(request.user),pk=notification_id)
    notifications.mark_read(request.user,item.pk)
    return redirect(destination(request))

@staff_view
def read_all(request):
    if request.method!='POST':return HttpResponseNotAllowed(['POST'])
    count=notifications.mark_all_read(request.user)
    messages.success(request,'Notifications marked as read.' if count else 'You have no unread notifications.')
    return redirect(destination(request))
