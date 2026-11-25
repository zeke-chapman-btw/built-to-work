from django.db import migrations

# How these guides read before the job-connection changes (Nov 2026). A guide is only
# rewritten if it still reads exactly like this, so guides an admin edited are left alone.
BEFORE = {'add-a-position': [('Open the customer', 'Customers → press the company.', 'text:Customers'),
                    ('Press + New position',
                     'Fill in the role, where the work is, pay, schedule and what experience is needed. Customers can '
                     'also request positions themselves from their portal.',
                     'text:+ New position'),
                    ('Save it and set up recruiting',
                     'Once saved, it shows in Recruiting → Positions, ready for candidates.',
                     '')],
 'approvals': [('Open Approvals',
                'Tabs across the top: event work requests, document checks, candidates waiting to be shared, and '
                'positions.',
                'text:Approvals'),
               ('Open an item and decide', 'Press Review on an item, check it, and approve or decline.', 'text:Review'),
               ('Look back at past decisions',
                "Show past decisions lists what's already been approved or declined.",
                'text:Show past decisions')],
 'connect-a-candidate': [('Open the position',
                          "Recruiting → Positions lists every open customer position. Open the one you're filling.",
                          'text:Match candidates'),
                         ('Press + Add candidate',
                          "You'll see suggested matches, best fit first. Use the filters to narrow them.",
                          'text:+ Add candidate'),
                         ('Review a match and select them',
                          "Press Review to look at the person, then Select candidate. They join the position's "
                          'pipeline.',
                          'text:Review||text:Select candidate'),
                         ('Move them through the stages',
                          'Open the candidate and press Change stage as things happen: contacted, interested, and so '
                          'on. Log contact records each conversation.',
                          'text:Change stage'),
                         ('Share them with the customer',
                          "When they're ready, press Share with employer, check what the customer will see, and press "
                          'Approve release. The customer gets an email and sees the candidate in their portal.',
                          'text:Share with employer'),
                         ("Watch for the customer's answer",
                          "The customer marks them Interested, Request Interview or Not Interested. You'll see it on "
                          "the candidate and get a reminder if they haven't answered.",
                          '')],
 'customer-request-position': [('Press + Request a position',
                                "It's on your dashboard and on the Positions page.",
                                'text:+ Request a position||text:Add a position'),
                               ('Fill in each section',
                                'The role, where the work is, pay and benefits, schedule, skills, and who we should '
                                'talk to. Press Continue after each section, or Save draft to finish later.',
                                'text:Continue'),
                               ('Submit it',
                                "We review it and start looking for candidates. You'll see it under Positions.",
                                '')],
 'customer-review-candidates': [('Open Candidates',
                                 'Waiting on you lists the candidates who need your answer.',
                                 'text:Waiting on you'),
                                ('Open a candidate',
                                 "You'll see their summary, work history, certifications, results and documents they "
                                 'agreed to share.',
                                 ''),
                                ('Choose your response',
                                 'Pick Interested, Request Interview or Not Interested, add a note if you like, and '
                                 'press Send to Built to Work. We take it from there.',
                                 'text:Your response')]}


def forwards(apps, schema_editor):
    from apps.training.content import GUIDES
    Guide = apps.get_model("training", "Guide")
    Step = apps.get_model("training", "GuideStep")
    latest = {g["slug"]: g for g in GUIDES}
    for slug, before in BEFORE.items():
        guide = Guide.objects.filter(slug=slug).first()
        if guide is None or slug not in latest:
            continue
        now = [(s.title, s.body, s.target) for s in Step.objects.filter(guide=guide).order_by("position", "pk")]
        if now != [tuple(x) for x in before]:
            continue
        Step.objects.filter(guide=guide).delete()
        for i, step in enumerate(latest[slug]["steps"]):
            Step.objects.create(guide=guide, position=i, **step)
        guide.summary = latest[slug].get("summary", guide.summary)
        guide.save()


class Migration(migrations.Migration):
    dependencies = [("training", "0005_training_message")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
