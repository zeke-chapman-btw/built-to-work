from django.db import migrations

def seed(apps,schema_editor):
    Category=apps.get_model('event_reports','ExpenseCategory')
    for name in ['Travel and lodging','Fuel','Tolls and parking','Event registration or venue fees','Supplies and materials','Equipment','Marketing and advertising','Gift cards and incentives','Other']:
        Category.objects.get_or_create(name=name,defaults={'incentive_purchase':name=='Gift cards and incentives'})

class Migration(migrations.Migration):
    dependencies=[('event_reports','0001_initial')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
