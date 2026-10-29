"""Keep ordinary staff roles out of native administrative data projections."""
from django.core.exceptions import PermissionDenied
from .roles import managed,manager
from .operational_access import limited_staff
class StaffAdministrationBoundary:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):return self.get_response(request)
    def process_view(self,request,view_func,view_args,view_kwargs):
        user=request.user
        match=request.resolver_match
        if not (user.is_authenticated and managed(user) and match and match.app_name=='admin'):return
        if match.url_name in ('login','logout'):return
        if user.is_superuser:return
        if limited_staff(user):raise PermissionDenied('Use your authorized BTW staff workspace.')
        model_admin=getattr(view_func,'model_admin',None)
        if model_admin:
            meta=model_admin.model._meta
            protected={'compensationrate','staffrole','expensecategory','checklistmaster','masterchecklistcategory','masterchecklistitem','trailer','trailerinstance','traileractivation','trailercredential','centralconfiguration','syncoperation','syncoutboxitem','backuprecord'}
            if meta.app_label in ('accounts','auth','core') or meta.model_name in protected:
                raise PermissionDenied('Administrator authority is required for protected configuration.')
