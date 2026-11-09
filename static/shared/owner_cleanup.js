/* Resolved dates come from the server's application timezone, never the device clock. */
document.addEventListener('click',function(event){
  const shortcut=event.target.closest('[data-due-date]');
  const assign=event.target.closest('[data-assign-self]');
  if(!shortcut&&!assign)return;
  const button=shortcut||assign,form=button.closest('form');if(!form)return;
  const field=form.querySelector(shortcut?'input[name="due_date"]':'select[name="assigned_staff"]');
  if(!field)return;field.value=shortcut?button.dataset.dueDate:button.dataset.assignSelf;
  field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));
  const status=form.querySelector('[data-date-choice-status]');if(shortcut&&status)status.textContent='Due '+button.dataset.dateLabel;
  field.focus();
});
