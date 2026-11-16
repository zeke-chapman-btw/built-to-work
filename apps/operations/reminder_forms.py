from django import forms


class PersonalReminderForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    activity_updates = forms.BooleanField(label='Activity updates', required=False)


class ReminderPolicyForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [('true', 'On'), ('false', 'Off')]
        if event:
            choices.insert(0, ('inherit', 'Company default'))
        for name, label in [('advance_enabled', 'Upcoming deadlines'),
                            ('activity_enabled', 'Activity updates'),
                            ('completion_enabled', 'Completed work')]:
            self.fields[name] = forms.TypedChoiceField(label=label, choices=choices,
                coerce=lambda value: None if value == 'inherit' else value == 'true')
        from . import event_reminder_policy as policy
        labels = {'staffing_enabled': 'Staffing gaps', 'preparation_enabled': 'Event preparation',
                  'closeout_enabled': 'Event closeout', 'staffing_reviews_enabled': 'Staffing requests',
                  'verification_reviews_enabled': 'Verification reviews', 'job_reviews_enabled': 'Job postings',
                  'customer_reviews_enabled': 'Customer approvals'}
        for name in (*policy.EVENT_BOOLEANS, *(() if event else policy.COMPANY_BOOLEANS)):
            self.fields[name] = forms.TypedChoiceField(label=labels[name], choices=choices,
                coerce=lambda value: None if value == 'inherit' else value == 'true')
        timing_labels = {'staffing_days': 'Staffing reminders',
                         'preparation_days': 'Preparation reminders', 'closeout_days': 'Closeout starts'}
        for name, values in policy.TIMINGS.items():
            options = []
            for value in values:
                key = ','.join(map(str, value)) if isinstance(value, tuple) else str(value)
                label = f'{value[0]} and {value[1]} days before' if isinstance(value, tuple) else f'{value} weekdays after'
                options.append((key, label))
            if event:
                options.insert(0, ('inherit', 'Company default'))
            self.fields[name] = forms.ChoiceField(label=timing_labels[name], choices=options)
        if not event:
            for category, name in policy.RECIPIENT_FIELDS.items():
                label = {'staffing': 'Staffing recipient', 'verification': 'Verification recipient',
                         'jobs': 'Job-posting recipient', 'customers': 'Customer-approval recipient'}[category]
                self.fields[name] = forms.ModelChoiceField(label=label, required=False,
                    queryset=policy.eligible_recipients(category), empty_label='Not assigned')
        review_fields = ['staffing_reviews_enabled']
        if not event:
            review_fields.extend(policy.COMPANY_BOOLEANS)
            review_fields.extend(policy.RECIPIENT_FIELDS.values())
        self.field_groups = [
            {'title': 'Task updates', 'fields': [self[name] for name in
                ('advance_enabled', 'activity_enabled', 'completion_enabled')]},
            {'title': 'Event work', 'fields': [self[name] for name in
                ('staffing_enabled', 'staffing_days', 'preparation_enabled', 'preparation_days',
                 'closeout_enabled', 'closeout_days')]},
            {'title': 'Review requests', 'fields': [self[name] for name in review_fields]}]

    def clean(self):
        cleaned = super().clean()
        from .event_reminder_policy import TIMINGS
        for name in TIMINGS:
            raw = cleaned.get(name)
            if raw:
                cleaned[name] = (None if raw == 'inherit' else
                                 [int(part) for part in raw.split(',')] if ',' in raw else int(raw))
        return cleaned


class WeekendWorkForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    work_date = forms.DateField(label='Weekend work date', required=False,
                               widget=forms.DateInput(attrs={'type': 'date'}))
