from django.core import signing

VERIFY_SALT = "participants.account.verify.v1"
SET_PASSWORD_SALT = "participants.account.password.v1"
EMAIL_CHANGE_SALT = "participants.account.email-change.v1"
TOKEN_MAX_AGE = 60 * 60 * 24

def make_token(value, salt):
    return signing.dumps(str(value), salt=salt)

def read_token(token, salt):
    try:
        return signing.loads(token, salt=salt, max_age=TOKEN_MAX_AGE)
    except (signing.BadSignature, signing.SignatureExpired, TypeError):
        return None
