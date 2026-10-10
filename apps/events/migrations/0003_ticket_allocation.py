from django.db import migrations, models
import uuid

class Migration(migrations.Migration):
    dependencies=[('events','0002_event_allow_station_auto_check_in')]
    operations=[
        migrations.CreateModel(name='TicketAllocationBlock',fields=[('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),('name',models.CharField(max_length=32,unique=True)),('start_number',models.BigIntegerField(unique=True)),('end_number',models.BigIntegerField()),('next_number',models.BigIntegerField()),('is_active',models.BooleanField(default=True)),('created_at',models.DateTimeField(auto_now_add=True))],options={'ordering':('start_number',)}),
        migrations.AddField(model_name='qrticket',name='ticket_number',field=models.CharField(blank=True,max_length=10,null=True,unique=True)),
    ]