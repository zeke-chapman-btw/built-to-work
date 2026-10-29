import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

class User(AbstractUser):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    quick_action_ids=models.JSONField(null=True,blank=True,default=None,editable=False)
    security_revision=models.PositiveIntegerField(default=0,db_default=0,editable=False)
    def _get_session_auth_hash(self,secret=None):
        # Existing sessions retain their hash until an explicit security event.
        if not self.security_revision:return super()._get_session_auth_hash(secret=secret)
        from django.utils.crypto import salted_hmac
        return salted_hmac('btw.staff.session.v1',self.password+':'+str(self.security_revision),secret=secret,algorithm='sha256').hexdigest()

class StaffInvitation(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    user=models.ForeignKey(User,on_delete=models.PROTECT,related_name='staff_invitations')
    issued_by=models.ForeignKey(User,on_delete=models.PROTECT,null=True,blank=True,related_name='staff_invitations_issued')
    purpose=models.CharField(max_length=8,choices=[('setup','Setup'),('reset','Reset')],default='setup')
    nonce=models.UUIDField(default=uuid.uuid4,editable=False)
    security_revision=models.PositiveIntegerField()
    created_at=models.DateTimeField(auto_now_add=True)
    expires_at=models.DateTimeField()
    accepted_at=models.DateTimeField(null=True,blank=True)
    revoked_at=models.DateTimeField(null=True,blank=True)

class StaffAccessAttempt(models.Model):
    key=models.CharField(primary_key=True,max_length=64)
    started_at=models.DateTimeField(default=timezone.now)
    count=models.PositiveIntegerField(default=0)
