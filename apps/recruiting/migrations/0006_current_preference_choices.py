from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('recruiting','0005_build15_employer')]
    operations = [
        migrations.AddField(model_name='recruitingprofile', name='current_employment_status', field=models.CharField(max_length=20, null=True, blank=True, default=None, choices=[('', 'Unknown'),('full_time','Full time'),('part_time','Part time'),('self_employed','Self employed'),('contract','Contract'),('unemployed','Unemployed')])),
        migrations.AddField(model_name='recruitingprofile', name='relocation_preference', field=models.CharField(max_length=5, null=True, blank=True, default=None, choices=[('', 'Unknown'),('yes','Yes'),('maybe','Maybe'),('no','No')])),
    ]
