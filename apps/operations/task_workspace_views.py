"""Progressively enhanced workspace and task creation endpoints."""
from urllib.parse import urlencode
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from apps.staffing.models import StaffProfile
from .views import staff_view
from . import workspace, task_access, quick_create
from .collaboration_forms import TaskLinksForm
from .models import Task

def workspace_page(request,mine=False):
    data=workspace.workspace(request.user,request.GET,mine=mine)
    page=Paginator(data.pop('queryset'),20).get_page(request.GET.get('page'))
    page.object_list=task_access.present_tasks(request.user,page.object_list)
    back=reverse('operations:tasks')+'?'+data['query']+('&page='+str(page.number) if page.number>1 else '')
    for task in page.object_list:task.detail_url=workspace.detail_url(task,back)
    allowed=data.pop('allowed')
    data.update(page_title='Tasks',tasks=page,can_create=True,
        people=StaffProfile.objects.filter(tasks__in=allowed).distinct() if data['managing'] else [],
        events=task_access.events_for(request.user).filter(Q(operational_tasks__in=allowed)|Q(task_links__task__in=allowed)).distinct() if data['managing'] else [],
        statuses=Task.Status.choices,priorities=Task.Priority.choices,
        quick_create_url=reverse('operations:quick-create')+'?'+urlencode({'return_to':back}))
    return render(request,'operations/tasks.html',data)

@staff_view
def quick_task(request,event_id=None,participant_id=None):
    modal=request.headers.get('X-Task-Modal')=='1'
    source=request.POST if request.method=='POST' else request.GET
    back=workspace.return_path(source.get('return_to',''))
    form,event,person=quick_create.prepare(request.user,request.POST if request.method=='POST' else None,
        event_id=event_id,participant_id=participant_id)
    if request.method=='POST' and form.is_valid():
        try:
            task,replayed=quick_create.create(request.user,form,event=event)
        except ValidationError as exc:form.add_error(None,exc)
        else:
            url=workspace.detail_url(task,back)
            if modal:
                response=JsonResponse({'url':url,'title':task.title,'replayed':replayed},status=200 if replayed else 201)
                response['Cache-Control']='no-store';return response
            messages.success(request,'Task already created.' if replayed else 'Task created.')
            return redirect(url)
    context_back=reverse('events:event_detail',args=[event.pk]) if event and request.user.has_perm('events.view_event') else (reverse('staffing:staff-event',args=[event.pk]) if event else (reverse('participants:staff-profile',args=[person.pk]) if person else back))
    context=dict(page_title='Add Task',form=form,context_event=event,context_participant=person,
        back=context_back,return_to=back,action=request.path,lookup_url=reverse('operations:task-record-lookup'))
    response=render(request,'operations/quick_form.html' if modal else 'operations/quick_create.html',context,status=400 if form.is_bound else 200)
    response['Cache-Control']='no-store';return response

@staff_view
def record_lookup(request):
    if request.method!='GET':raise PermissionDenied
    kind=request.GET.get('kind','')
    if kind not in ('events','participants'):return JsonResponse({'results':[]},status=400)
    q=request.GET.get('q','').strip()[:150]
    form=TaskLinksForm(actor=request.user,event_query=q if kind=='events' else '',participant_query=q if kind=='participants' else '')
    records=form.fields[kind].queryset
    response=JsonResponse({'results':[{'id':str(obj.pk),'label':obj.name if kind=='events' else str(obj)} for obj in records]})
    response['Cache-Control']='no-store';return response
