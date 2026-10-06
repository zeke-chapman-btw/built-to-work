from django.db import migrations

def add_duck_hunt(apps, schema_editor):
    Definition = apps.get_model('games', 'GameDefinition')
    Definition.objects.get_or_create(key='duck_hunt', defaults={'display_name': 'Duck Hunt', 'implementation_key': 'duck_hunt', 'active': True, 'implementation_version': '1'})

def remove_duck_hunt(apps, schema_editor):
    Definition = apps.get_model('games', 'GameDefinition')
    Definition.objects.filter(key='duck_hunt').delete()

class Migration(migrations.Migration):
    dependencies = [('games', '0001_initial')]
    operations = [migrations.RunPython(add_duck_hunt, remove_duck_hunt)]
