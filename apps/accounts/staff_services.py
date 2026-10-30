"""Locked and audited staff account lifecycle; no automatic role enrollment."""
import hashlib,json,uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from apps.core.audit import record_audit
from apps.staffing.models import StaffProfile
from .models import StaffInvitation,StaffAccessAttempt
from .roles import ROLE_GROUPS,roles_for,require_account_manager,require_target,manager,lock_role_groups
INVITE_SALT='btw.staff.invitation.v1'

def account_state(user):
    return {'username':user.username,'email':user.email,'first_name':user.first_name,'last_name':user.last_name,'active':user.is_active,'staff':user.is_staff,'administrator':user.is_superuser,'roles':sorted(roles_for(user)),'groups':list(user.groups.order_by('pk').values_list('pk',flat=True)),'permissions':list(user.user_permissions.order_by('pk').values_list('pk',flat=True)),'security_revision':user.security_revision,'profile_id':str(StaffProfile.objects.filter(user=user).values_list('pk',flat=True).first() or '')}
def revision(user):
    state=account_state(user)
    profile=StaffProfile.objects.filter(user=user).first()
    state['profile']=str(profile.pk) if profile else None
    state['profile_revision']=profile.updated_at.isoformat() if profile else None
    return signing.salted_hmac('staff.account.revision',json.dumps(state,sort_keys=True,default=str)).hexdigest()
def audit(actor,action,user,before=None,reason=''):
    return record_audit(actor=actor,action='staff.account.'+action,instance=user,source='staff_access',old_data=before,new_data=account_state(user),reason=reason)
def fresh_actor(actor):return get_user_model().objects.select_for_update().get(pk=actor.pk)
def lock_users(actor,target_id=None):
    ids={actor.pk}
    if target_id:ids.add(target_id)
    users={u.pk:u for u in get_user_model().objects.select_for_update().filter(pk__in=ids).order_by('pk')}
    return users[actor.pk],users.get(target_id)
def require_reason(value):
    value=str(value or '').strip()
    if not value:raise ValidationError('A reason is required.')
    return value

def check_fields(data):
    if any(k in data for k in ('groups','user_permissions','is_superuser','is_staff','security_revision','password','password1','password2')):raise PermissionDenied('Use the authorized account fields.')

@transaction.atomic
def save_account(*,actor,data,user_id=None):
    check_fields(data);lock_role_groups();actor,target=lock_users(actor,user_id);require_account_manager(actor)
    if user_id and target is None:raise ValidationError('Account not found.')
    from apps.customers.access import customer_identity
    if target and customer_identity(target):raise PermissionDenied('Prepare customer users through Customers; staff conversion is not permitted.')
    if target:
        require_target(actor,target)
        if data.get('revision')!=revision(target):raise ValidationError('This account changed. Reload before saving.')
    from apps.participants.models import ParticipantAccount
    if target and ParticipantAccount.objects.filter(user=target).exists():raise PermissionDenied('Staff and participant identities must remain separate.')
    reason=require_reason(data.get('reason'))
    selected=set(data.get('roles') or [])
    allowed=set(ROLE_GROUPS)|{'administrator'} if actor.is_superuser else {'recruiter','event_staff'}
    if not selected or selected-allowed:raise PermissionDenied('Choose roles within your authority.')
    for role in selected-{'administrator'}:
        if not Group.objects.filter(name=ROLE_GROUPS[role]).exists():raise ValidationError('Staff roles must be configured by an Administrator before account changes.')
    before=account_state(target) if target else None
    username=str(data.get('username','')).strip();email=str(data.get('email','')).strip().lower()
    if not username or not email:raise ValidationError('Username and email are required.')
    User=get_user_model()
    if User.objects.filter(username=username).exclude(pk=target.pk if target else None).exists():raise ValidationError('Choose another username.')
    if User.objects.filter(is_staff=True,email__iexact=email).exclude(pk=target.pk if target else None).exists():raise ValidationError('This email needs identity review; no accounts were linked.')
    if not data.get('confirm_identity'):raise ValidationError('Confirm the staff identity and account link.')
    if target and target.is_superuser and ('administrator' not in selected or not data.get('is_active',target.is_active)):
        administrators=list(User.objects.select_for_update().filter(is_superuser=True,is_active=True,is_staff=True).order_by('pk'))
        if len(administrators)<=1:raise ValidationError('Retain at least one active Administrator.')
    new=target is None
    target=target or User(username=username,is_staff=True,is_active=False)
    target.username=username;target.email=email
    target.first_name=str(data.get('first_name','')).strip();target.last_name=str(data.get('last_name','')).strip()
    target.is_staff=True;target.is_superuser='administrator' in selected
    if not new:target.is_active=bool(data.get('is_active',target.is_active))
    target.security_revision+=1
    if new:target.set_unusable_password()
    target.full_clean(exclude=['password']);target.save()
    # Preserve unrelated legacy groups/direct grants. Only an Administrator can
    # operate such a protected account; ordinary controls cannot edit grants.
    target.groups.remove(*Group.objects.filter(name__in=ROLE_GROUPS.values()))
    target.groups.add(*Group.objects.filter(name__in=[ROLE_GROUPS[x] for x in selected if x in ROLE_GROUPS]))
    profile_id=getattr(data.get('profile'),'pk',data.get('profile'))
    existing=StaffProfile.objects.select_for_update().filter(user=target).first()
    if profile_id:
        profile=StaffProfile.objects.select_for_update().get(pk=profile_id)
        if profile.user_id not in (None,target.pk):raise PermissionDenied('This profile is already linked to another account.')
        if existing and existing.pk!=profile.pk:raise PermissionDenied('Review the existing account link before relinking.')
    else:profile=existing or StaffProfile(first_name=target.first_name or username,last_name=target.last_name,email=email)
    profile.user=target;profile.full_clean();profile.save()
    StaffInvitation.objects.filter(user=target,accepted_at__isnull=True,revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit(actor,'created' if new else 'updated',target,before,reason)
    return target

@transaction.atomic
def set_active(*,actor,user_id,active,expected_revision,reason):
    lock_role_groups();actor,target=lock_users(actor,user_id);require_target(actor,target)
    if expected_revision!=revision(target):raise ValidationError('This account changed. Reload before proceeding.')
    reason=require_reason(reason)
    if target.is_superuser and not active and get_user_model().objects.filter(is_superuser=True,is_active=True,is_staff=True).count()<=1:raise ValidationError('Retain an active Administrator.')
    before=account_state(target);target.is_active=active;target.security_revision+=1;target.save(update_fields=['is_active','security_revision'])
    StaffInvitation.objects.filter(user=target,accepted_at__isnull=True,revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit(actor,'reactivated' if active else 'deactivated',target,before,reason);return target

@transaction.atomic
def rate_limit(scope,value,limit=10,minutes=15):
    key=signing.salted_hmac('staff.access.rate',scope+':'+str(value)).hexdigest()
    row,_=StaffAccessAttempt.objects.get_or_create(key=key)
    row=StaffAccessAttempt.objects.select_for_update().get(pk=key);now=timezone.now()
    if row.started_at<now-timedelta(minutes=minutes):row.started_at=now;row.count=0
    row.count+=1;row.save();return row.count<=limit

@transaction.atomic
def issue_invitation(*,user,actor=None,purpose='setup',reason='Account setup',expected_revision=None):
    lock_role_groups()
    if actor:
        actor,target=lock_users(actor,user.pk);require_target(actor,target)
        if expected_revision!=revision(target):raise ValidationError('This account changed. Reload before inviting.')
    else:target=get_user_model().objects.select_for_update().get(pk=user.pk)
    if actor is None and purpose!='reset':raise PermissionDenied
    from apps.customers.access import customer_identity
    if customer_identity(target):raise PermissionDenied('Customer activation and invitations are not available in Build 13.')
    if not target.is_staff or not target.email or (purpose=='reset' and not target.is_active):raise ValidationError('Account setup is unavailable.')
    if purpose=='setup' and target.is_active and target.has_usable_password():raise ValidationError('Use password recovery for an active account.')
    StaffInvitation.objects.filter(user=target,purpose=purpose,accepted_at__isnull=True,revoked_at__isnull=True).update(revoked_at=timezone.now())
    invitation=StaffInvitation.objects.create(user=target,issued_by=actor,purpose=purpose,security_revision=target.security_revision,expires_at=timezone.now()+timedelta(hours=24))
    audit(actor,'invitation_issued' if purpose=='setup' else 'recovery_requested',target,reason=require_reason(reason));return invitation

def invitation_token(invitation):
    return signing.dumps({'id':str(invitation.pk),'nonce':str(invitation.nonce),'revision':invitation.security_revision,'proof':default_token_generator.make_token(invitation.user),'email':hashlib.sha256(invitation.user.email.lower().encode()).hexdigest()},salt=INVITE_SALT)
def read_invitation(token,lock=False):
    try:
        payload=signing.loads(token,salt=INVITE_SALT,max_age=86400)
        qs=StaffInvitation.objects.select_for_update(of=('self',)) if lock else StaffInvitation.objects
        item=qs.select_related('user','issued_by').get(pk=payload['id'],accepted_at__isnull=True,revoked_at__isnull=True)
        user=item.user
        if not default_token_generator.check_token(user,payload.get('proof','')) or item.expires_at<=timezone.now() or str(item.nonce)!=payload['nonce'] or user.security_revision!=item.security_revision or payload['revision']!=item.security_revision or not user.is_staff or hashlib.sha256(user.email.lower().encode()).hexdigest()!=payload['email']:return None
        if item.purpose=='reset' and not user.is_active:return None
        if item.issued_by and (not manager(item.issued_by) or (not item.issued_by.is_superuser and (user.is_superuser or 'manager' in roles_for(user)))):return None
        return item
    except (signing.BadSignature,ValueError,TypeError,KeyError,StaffInvitation.DoesNotExist):return None

@transaction.atomic
def accept_invitation(*,token,password):
    item=read_invitation(token)
    if not item:raise ValidationError('This link is invalid, expired or already used.')
    lock_role_groups()
    ids=[item.user_id]+([item.issued_by_id] if item.issued_by_id else [])
    locked={u.pk:u for u in get_user_model().objects.select_for_update().filter(pk__in=ids).order_by('pk')}
    user=locked[item.user_id]
    item=read_invitation(token,lock=True)
    if not item:raise ValidationError('This link is invalid, expired or already used.')
    validate_password(password,user)
    before=account_state(user);user.set_password(password);user.is_active=True;user.security_revision+=1;user.save(update_fields=['password','is_active','security_revision'])
    item.accepted_at=timezone.now();item.nonce=uuid.uuid4();item.save(update_fields=['accepted_at','nonce'])
    StaffInvitation.objects.filter(user=user,accepted_at__isnull=True,revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit(user,'password_setup' if item.purpose=='setup' else 'password_reset',user,before,'Account holder completed secure password setup.');return user
