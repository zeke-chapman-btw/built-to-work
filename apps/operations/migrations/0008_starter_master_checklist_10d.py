"""A small optional starter checklist, not a mandatory operational policy."""
from django.db import migrations


def seed_master(apps,schema_editor):
    alias=schema_editor.connection.alias
    Master=apps.get_model("operations","ChecklistMaster")
    Category=apps.get_model("operations","MasterChecklistCategory")
    Item=apps.get_model("operations","MasterChecklistItem")
    master,created=Master.objects.using(alias).get_or_create(key="btw",defaults={"title":"BTW event checklist"})
    if not created:return
    categories=[
        ("Pre-event preparation","prepare",["Assign event responsibilities","Review event operational setup"]),
        ("Arrival and setup","arrival",["Review planned staff and station arrangements","Review arrival and setup tasks"]),
        ("Live event operations","live",["Review event tasks during operations","Record operational follow-ups"]),
        ("Closeout and pack-up","closeout",["Review pack-up and return tasks","Review remaining closeout follow-ups"]),
    ]
    for position,(title,phase,items) in enumerate(categories):
        category=Category.objects.using(alias).create(master=master,title=title,phase=phase,position=position)
        for order,title in enumerate(items):
            Item.objects.using(alias).create(category=category,title=title,position=order,is_required=False)


class Migration(migrations.Migration):
    dependencies=[("operations","0007_event_execution_checklists_10d")]
    operations=[migrations.RunPython(seed_master,migrations.RunPython.noop)]
