import uuid
from django.db import migrations,models
import django.db.models.deletion

class Migration(migrations.Migration):
 dependencies=[('operations','0008_starter_master_checklist_10d'),('customers','0001_initial')]
 operations=[migrations.CreateModel(name='TaskCustomerLink',fields=[
  ('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
  ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
  ('task',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='customer_links',to='operations.task')),
  ('organization',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='task_links',to='customers.customerorganization'))],
  options={'constraints':[models.UniqueConstraint(fields=('task','organization'),name='operations_unique_task_customer_link')]})]
