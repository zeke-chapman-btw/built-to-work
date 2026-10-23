"""Existing trailer display settings only; credential workflows stay superuser-only."""
import hashlib,json
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.events.models import TrailerInstance
from apps.events.services import audit_action
from .presentation import list_page
from apps.internal_ui.section_editors import operations_return, operations_editor, operations_saved


def snapshot(obj):
    return dict(identity=obj.identity,display_name=obj.display_name,is_active=obj.is_active)


def revision(obj):
    return hashlib.sha256(json.dumps(snapshot(obj),sort_keys=True).encode()).hexdigest()


class TrailerForm(forms.ModelForm):
    revision=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(max_length=500,widget=forms.Textarea(attrs={"rows":2}),label="Change reason")
    class Meta:
        model=TrailerInstance
        fields=("identity","display_name","is_active")
        labels={"identity":"Trailer ID","display_name":"Name","is_active":"Active"}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["identity"].disabled=True
        self.initial["revision"]=revision(self.instance)


def authorize(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff or not user.is_superuser:raise PermissionDenied


@login_required(login_url="/admin/login/")
@require_http_methods(["GET"])
def trailer_list(request):
    authorize(request.user)
    rows,filters=list_page(request,TrailerInstance.objects.order_by("display_name"),search_fields=("display_name","identity"),name_field="display_name",
        active_field="is_active",status_choices=(("active","Active"),("inactive","Inactive")))
    return render(request,"operations/trailers.html",{"page_title":"Trailers","trailers":rows,**filters})


@login_required(login_url="/admin/login/")
@require_http_methods(["GET","POST"])
@transaction.atomic
def trailer_edit(request,trailer_id):
    authorize(request.user)
    obj=get_object_or_404(TrailerInstance.objects.select_for_update() if request.method=="POST" else TrailerInstance.objects.all(),pk=trailer_id)
    before=snapshot(obj);current=revision(obj)
    form=TrailerForm(request.POST if request.method=="POST" else None,instance=obj)
    if request.method=="POST" and form.is_valid():
        if form.cleaned_data["revision"]!=current:form.add_error(None,"This trailer changed after you opened it. Reload before saving.")
        else:
            saved=form.save()
            audit_action(actor=request.user,action="operations.trailer.updated",instance=saved,
                old_data=before,new_data=snapshot(saved),reason=form.cleaned_data["reason"])
            messages.success(request,"Trailer display settings saved. Credentials and trailer identity were preserved.")
            return operations_saved(request,operations_return(request,reverse("operations:trailers")))
    return operations_editor(request,form,title="Edit trailer",back=operations_return(request,reverse("operations:trailers")))
