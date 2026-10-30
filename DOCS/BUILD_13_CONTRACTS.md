# Build 13 implementation contracts

Baseline: `21d8e70ddd7c8d23cfaff2679a73d5ae9e46b608`, from `origin/render-preview`.
Candidate: `/workspaces/btw-build-13-customers`, `build-13-customer-management`.
No commit, push, merge, shared-database change or deployment is authorized.

## Authority and identity

Only fresh, active Administrator/Manager identities may read or change customer
data. Existing role definitions are extended with one customer-specific Manager
permission (version 2); Recruiter and Event Staff grants are unchanged.
Connected organizations confer no inherited access or entitlements.

CustomerIdentity owns prepared business identity information and references the
existing AUTH_USER_MODEL. Its technical authentication record has a generated
username, blank authentication email, inactive/non-staff/non-superuser flags and
an unusable password. No invitation, token, portal activation or login is added.
The profile's normalized email is unique; matching emails never merge accounts.
Explicit selection is required to reuse an existing customer identity in another
organization. Database guards prevent customer identity conversion, staff grants,
staff invitations and participant links. Native customer administration is read-only.

## Seats and Primary Lead

An organization starts as an unprovisioned prospect. Allowances are null until
staff configure them; null does not mean unlimited and zero is valid. Enabled
(prepared) memberships consume the selected role's allowance; suspended/removed
memberships do not. Removal is reversible and never changes another membership
or the authentication identity.

The first enabled Lead explicitly provisions the organization and becomes its
Primary Lead. Thereafter the designated membership must remain an enabled Lead.
Changes lock the organization and verify its expected revision. Reducing limits
does not remove users. Capacity validation rejects changes that increase an
over-cap or unconfigured role count; seat-neutral transfers remain possible.

A transfer normally retains the former Primary Lead as an additional Lead. A
seat-neutral role swap is available only through an explicit choice to make the
former Primary Lead a Member, with a reason. It is never a silent demotion.
Database uniqueness, role checks, scoped relationships and Primary Lead guards
complement service-level locked aggregate capacity validation. An unconditional
database `usage <= limit` constraint would incorrectly prohibit approved limit
reductions below current usage and is intentionally not used.

Writes first acquire the existing staff-security lock in its established order,
then the fresh actor and organization/identity rows. This deliberately coarse
foundation lock prevents cross-company identity/provisioning deadlocks and races
with role revocation; it also serializes writes across organizations. No shared
or live role configuration is applied during this build.

## Entitlements

Organization entitlements are separate from membership roles and actual feature
availability. New Workforce Need creation requires its explicit organization
entitlement; disabling it does not close or hide existing Needs. Staff can still
manage existing records. Candidate matching, customer directory search, expanded
profiles and candidate contact are future intent only and expose no records.
Individual directory-search seat limits are deferred until an individual
permission/counting model exists. No commercial plans, defaults, prices or billing.

## Workforce Need validation and revision rules

One stable Need represents one hiring requirement; it has typed hiring revisions
and a nullable Campaign FK. Drafts may be incomplete, but supplied structured
values must be valid. Submission requires every topic below to be answered:

| Topic | Submission contract |
| --- | --- |
| Role | Title, trade and meaningful description; positive whole worker quantity |
| Employment | Employment type; explanation when Other |
| Location | Active same-org location with complete address snapshot, meaningful custom address, or explicit remote details |
| Compensation | Nonnegative rate/range, three-letter currency and pay basis; or explicit N/A explanation without artificial amounts |
| Overtime | Yes with details, No, or N/A with explanation |
| Benefits / per diem | Provided with details, None, or N/A with explanation; any numeric amount must be nonnegative |
| Schedule | Shift, schedule details and positive weekly hours up to 168, or explained variable hours |
| Start / duration | Specific start date or meaningful timeframe; duration, ongoing or explained N/A; end cannot precede start |
| Required / preferred skills | Provided details, None, or explained N/A for each |
| Required / preferred experience | Provided details and optional nonnegative years, None, or explained N/A for each |
| Required / preferred credentials | Provided license/certification details, None, or explained N/A for each |
| Travel / overnight | Explicit Yes/No/N/A; details for Yes or N/A |
| Conditions / equipment | Provided details, None, or explained N/A for each |
| Primary contact | Active same-org business contact; no login account required |
| Screening / other requirements | Provided details, None, or explained N/A for each |
| Hiring timeline | Meaningful hiring timeframe; optional validated deadline |

An unanswered choice is distinct from explicit None/N/A. No legal wage policy,
automatic pay calculation or default compensation is invented. BTW review remains
responsible for evaluating the supplied hiring terms.

All hiring-term changes are material. The narrow administrative allowlist is the
stable Need's internal reference and internal note; editing either never modifies
or discards pending hiring revisions. There is no user-selected minor bypass.

Submission snapshots location/contact information. Submitted payloads freeze;
approved, returned and withdrawn historical revisions remain immutable. An
approved effective revision remains in force while a separate draft/submitted
revision is pending. Approvals require the actual expected revision and record
versions. Approval never resumes an On Hold or Closed Need.

Initial lifecycle: Draft -> Submitted for Review -> Under BTW Review -> Approved /
Active. Return/withdraw preserves the revision and permits a new working draft.
Active -> On Hold -> explicit resume. Active/On Hold -> manual Closed/Completed.
Nonterminal Needs may be cancelled. Closing records actor, time and reason.
Closed/Cancelled are not generically reopened; pending approvals do not reopen them.

## Campaigns

Planning details and associated Need scope have a planning version. Approval is a
separate explicit Approve/Activate action against that reviewed version. Generic
status edits cannot activate. Material edits/association changes invalidate prior
approval; an active Campaign goes On Hold pending review. Approval never approves
individual Needs. Completed/Cancelled Campaigns are terminal in this foundation.
One Need belongs to zero or one same-org Campaign; transfers are locked and audited.

## Audit and UI

Use existing record_audit/AuditLog with a small indexed organization association.
The native global audit viewer also excludes customer entries for unauthorized
legacy audit viewers, while retaining their existing access to unrelated audits.
Store statuses, field names, revision IDs, limits and reasons, not full hiring
payloads, passwords or tokens. GETs never write history or change workflows.
Customer archive/restore affects no Need, Campaign, membership or connected org.
Archived organizations are read-only until explicitly restored.

Eight tabs: Overview, Company Profile, Workforce Needs, Campaigns, Matches, Users &
Contacts, Access & Plan, History. Ordinary content follows the 920px shared UI
standard, real links, visible focus, 44px actions, consistent card padding and
accessible tab overflow at 360/390px. Global Campaigns uses this same domain;
global Approvals remains planned. No fake matches or permanent placeholder counts.

Build 14 Lead-managed Members/customer portal, Build 15 matching/recruiting and
later participant relationship workflows are documented only, not implemented.
