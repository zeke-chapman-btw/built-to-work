from django.db import migrations

STAGES=[('identified','Identified','active'),('contacting','Contacting','active'),
    ('interested','Interested','active'),('qualification_review','Qualification Review','active'),
    ('ready_for_employer','Ready for Employer','active'),('submitted','Submitted','active'),
    ('employer_reviewing','Employer Reviewing','active'),('interview','Interview','active'),
    ('hired','Hired','hired'),('not_selected','Not Selected','not_selected'),('withdrawn','Withdrawn','withdrawn')]


def seed(apps,schema_editor):
    Stage=apps.get_model('recruiting','RecruitingStage')
    for index,(key,label,semantic) in enumerate(STAGES):
        Stage.objects.get_or_create(key=key,defaults={'label':label,'semantic':semantic,'position':index*10})


class Migration(migrations.Migration):
    dependencies=[('recruiting','0002_build15_matching')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
