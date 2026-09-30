# Architecture

## Foundation

The project is a Django 5.2 LTS application with environment-specific settings
under `config.settings`. `apps.accounts.User` is the custom authentication
model and must remain configured as `AUTH_USER_MODEL` before the first
migration. Authorization uses Django's built-in Groups and Permissions; no
parallel role model is introduced.

`apps.core` owns framework-level primitives only: abstract UUID, timestamp, and
optional archive bases; the explicit `AuditLog` record; and basic service
health endpoints. Audit writes go through `record_audit` at the point where a
caller knows the actor, action, and reason. Audit behavior must not be attached
to model save signals.

## Future domain boundaries

Future product domains should be separate Django apps with explicit ownership
of their models and behavior. Cross-domain references should use stable model
relationships and documented service/API boundaries; `apps.core` must not
become a home for domain workflows. No participant, event, recruiting,
customer, quiz, simulator, billing, integration, or synchronization behavior is
part of this foundation.

## Locked rules

- Keep the custom UUID user model and `AUTH_USER_MODEL` in place from the
  beginning; do not swap user models after migrations exist.
- Use Django Groups and Permissions for authorization unless a future approved
  requirement changes this rule.
- Record audit events explicitly with `record_audit`; never infer audit
  records through save signals.
- Preserve environment-specific configuration. Production must keep `DEBUG`
  off, require `SECRET_KEY` and `ALLOWED_HOSTS`, redirect HTTP to HTTPS, use
  secure session and CSRF cookies, and enable HSTS.
- Keep secrets out of source control. `.env.example` contains placeholders;
  production secrets are supplied by the runtime environment.
- Keep this foundation free of unapproved business-domain workflows.

## Audit data handling

Audit entries are created explicitly with record_audit; model saves are not audited automatically. Callers should record only meaningful fields needed to explain a change, not serialize entire models or database objects. Never intentionally place passwords or password hashes, authentication/session tokens or identifiers, API keys, secret keys, reset or verification tokens, raw credentials, or other authentication secrets in old_data or new_data. Sensitive participant or customer information may be recorded only when genuinely required for the audit purpose, and must be minimized. Audit data must not become a secondary unrestricted copy of sensitive records.

The current AuditLog is an application audit trail for operational review. It is not cryptographically tamper-proof evidence; users with sufficient database or infrastructure access can alter it. No tamper-protection mechanism is part of this phase.

## Health endpoint semantics

/health/ is a liveness endpoint. A successful response confirms that the Django application process can respond. It does not check database availability, external services, trailer/cloud synchronization, HubSpot, email, or SMS. Do not treat it as a readiness or dependency-health check.

## Production deployment review

Before deploying, verify TLS termination and proxy behavior end to end. Configure SECURE_PROXY_SSL_HEADER only when the trusted proxy reliably sets and sanitizes the corresponding header; review SECURE_SSL_REDIRECT to avoid redirect loops. Confirm production ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS. Review the HSTS duration, includeSubDomains, and preload suitability for the actual domain and every affected subdomain before enabling preload. Keep production protections enabled while performing this review.

## Development platform scope

Current development instructions assume a Unix-like environment or GitHub Codespaces. Windows and trailer deployment instructions will be added when that deployment phase is implemented.

## Django Admin

Django Admin is the internal back-office interface at /admin/. It is for authorized staff and development review; it is not a customer or participant portal. The custom user model is registered with Django's UserAdmin. AuditLog is available for inspection and is read-only through Admin.
