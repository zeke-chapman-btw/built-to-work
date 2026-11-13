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


class WeekendWorkForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    work_date = forms.DateField(label='Weekend work date', required=False,
                               widget=forms.DateInput(attrs={'type': 'date'}))
