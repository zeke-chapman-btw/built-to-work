"""Focused, opt-in presentation over existing authorized participant controllers."""
from django.shortcuts import render
from django.urls import reverse
from apps.internal_ui.record_editors import editor_response

MANAGERS = {
    'participants/staff_documents.html', 'participants/staff_document_edit.html',
    'participants/staff_document_replace.html', 'participants/staff_qualifications.html',
    'participants/staff_profile.html', 'participants/note_history.html', 'participants/manager_action_conflict.html',
    'participants/qualification_approval_detail.html', 'participants/document_history_manager.html',
}

def manager_response(request, template, context, **kwargs):
    if request.headers.get('X-BTW-Editor') != '1' or template not in MANAGERS:
        return None
    if not context.get('participant'):
        return None
    ctx = dict(context, profile_manager=True, people_base='participants/manager_base.html')
    ctx.setdefault('page_title', 'Participant information')
    focused_form = template in {'participants/staff_document_edit.html','participants/staff_document_replace.html'} or (template=='participants/staff_qualifications.html' and (ctx.get('is_edit') or (ctx.get('can_add') and (request.GET.get('add') or request.method=='POST'))))
    if focused_form:
        if template=='participants/staff_qualifications.html':
            ctx['page_title']='Edit qualification' if ctx.get('is_edit') else 'Add qualification'
            ctx['editor_context']=str(ctx['participant'])
        ctx.update(back=ctx['people_return_url'],save_label='Save changes',fields_template='internal/ui/section_fields.html',cautious_save=True,editor_return_label='profile',full_page_editor=False)
        return editor_response(request,template=template,context=ctx,panel='internal/ui/record_form_panel.html')
    if template == 'participants/staff_profile.html':
        section = ctx.get('section')
        if section not in {'career','qualifications','documents','assessments','events','source-event','acquisition','notes','activity'}:
            return None
        ctx['page_title'] = {'career':'Work & reported experience','acquisition':'Original submitted answers',
            'activity':'Activity history','source-event':'Event information'}.get(section, section.title())
        if section in {'career','assessments','events','source-event','acquisition','activity'}:
            template='participants/profile_data_manager.html'
    response = render(request, template, ctx, status=409 if ctx.get('save_conflict') else kwargs.get('status',400 if ctx.get('form') and ctx['form'].is_bound and ctx['form'].errors else 200))
    response['X-BTW-Editor'] = '1'
    response['Cache-Control'] = 'no-store'
    return response
