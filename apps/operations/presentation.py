"""Shared list presentation; source querysets and endpoint authorization stay authoritative."""
from django.core.paginator import Paginator
from django.db.models import Q


def list_page(request,queryset,*,search_fields,name_field="name",newest_field="-created_at",
              status_field=None,status_choices=(),active_field=None,page_size=25):
    q=request.GET.get("q","").strip()[:200]
    status=request.GET.get("status","")
    if status not in {str(value) for value,label in status_choices}:status=""
    sort=request.GET.get("sort","default")
    if sort not in ("default","name","newest"):sort="default"
    if q:
        query=Q()
        for field in search_fields:query|=Q(**{field+"__icontains":q})
        queryset=queryset.filter(query)
    if status and active_field:queryset=queryset.filter(**{active_field:status=="active"})
    elif status and status_field:queryset=queryset.filter(**{status_field:status})
    if sort=="name":queryset=queryset.order_by(name_field,"pk")
    elif sort=="newest":queryset=queryset.order_by(newest_field,"pk")
    page=Paginator(queryset,page_size).get_page(request.GET.get("page"))
    return page,dict(q=q,selected_status=status,selected_sort=sort,status_choices=status_choices)
