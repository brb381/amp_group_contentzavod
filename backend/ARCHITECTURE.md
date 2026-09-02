# Architecture

## Main rule

PostgreSQL is the source of truth. Redis/Celery transports commands but does not own
business state. A command reaching a worker is ready to execute; the worker does not
decide whether the job should have been scheduled.

## Processes

```text
React client -> FastAPI -> PostgreSQL
                         |
                         v
                    scheduler -> Redis/Celery -> email worker -> SMTP
                              -> Redis/Celery -> YouTube worker -> YouTube API
                              -> Redis/Celery -> calculation worker -> PostgreSQL
                              -> Redis/Celery -> export worker -> S3/MinIO
deployment cron -> creator retention command -> PostgreSQL
```

- API validates HTTP input, authorizes the caller and commits business changes.
- Scheduler admits due database records to a named queue. Email, YouTube and
  calculation dispatchers have separate error boundaries.
- Email worker only claims and sends a prepared email command.
- YouTube worker only claims a prepared batch, calls YouTube and records the outcome.
- Calculation worker only claims one prepared closed-period job and writes its snapshot.
- Export worker reads one bounded report snapshot, generates CSV/XLSX and uploads it.
- PostgreSQL stores leases, retries, quota reservations and provider availability.
- Redis is disposable transport. Losing Redis may delay work but must not lose the
  database record that represents it.

Each process has its own settings model and receives only its required secrets.

## Queue contracts

Task names, queue names and Pydantic payloads live in `app/contracts.py`. Producers and
consumers must use those constants and reject unknown payload fields.

### Email

```text
pending -> processing -> delivering -> dispatched
                 |              |
                 +----failure---+-> pending
```

The API creates the outbox record in the same transaction as the user-facing change.
The scheduler assigns a new `dispatch_id` and a lease before publishing. The worker
atomically claims the matching `(event_id, dispatch_id)` before SMTP. A command from an
expired dispatch is ignored.

SMTP cannot provide exactly-once delivery. A crash after SMTP accepts a message but
before PostgreSQL commits can still cause a duplicate after lease recovery. Consumers
of email should therefore tolerate rare duplicate messages.

### YouTube

```text
pending/retry_wait -> queued -> processing -> succeeded
                                  |
                                  +-> retry_wait/failed
```

Only one batch of at most 50 videos is active. The scheduler reserves one quota unit
per `videos.list` call before publishing. The worker does not retry through Celery. It
records retry time or provider blocking in PostgreSQL, and the scheduler sees that
state on its next iteration.

Daily quota is tracked by the YouTube Pacific-time quota date. Temporary `429` and
provider failures use `Retry-After` when available, otherwise bounded backoff.

### View readings

Manual readings are accepted from the 25th through month end in Moscow time and remain
pending until a reviewer accepts, rejects or corrects them. The reported value is never
overwritten by a review correction; accepted value and immutable action history are
stored separately.

Approval of a YouTube publication creates an immediate baseline collection job. From
the 25th through the last day, scheduler creates one daily job per approved publication
after the configured Moscow collection hour. Metadata and view commands share the same
provider lock and daily quota reservation, so separate command types cannot multiply
YouTube request concurrency.

Normal API readings are accepted automatically. A decreasing total requires manual
review; unusual monthly growth is accepted with a risk flag for period review. Jobs and
readings have database uniqueness keys, so queue redelivery cannot duplicate a daily
measurement.

### Billing calculations

The scheduler creates missing closed months in chronological order and publishes one
prepared command to the dedicated `calculations` queue. The calculation worker only
claims that command and replaces a preliminary snapshot: publication accruals, creator
totals and the period total. Rates and money are immutable integer-kopek snapshots.
Rate creation and period creation serialize on the single `billing_control` row, so a
period cannot race ahead using an obsolete rate.

`reading_dataset_revision` is the transaction barrier between readings and billing.
Every reading mutation locks and increments it. Calculation stores the revision it
used; confirmation locks it again and rejects a stale result. Confirmation credits all
creator balances, appends one idempotent ledger row per creator, locks closed readings
and advances `closed_through_period` in one transaction. A late YouTube result for a
closed month is rejected. Corrections to confirmed accruals are append-only ledger
deltas and never rewrite the original calculation.

### Payouts

Payouts own their workflow but do not own the creator wallet. They call the narrow
wallet operations `reserve_payout`, `release_payout` and `settle_payout`; billing does
not import the payout module. Monetary state changes are integer kopecks and commit in
one database transaction with the request transition, immutable payout event, balance
ledger entry and security audit event.

```text
requested -> under_review -> approved -> paid
     |             |             |
     +-------------+-------------+-> rejected
```

Receipt state is derived separately from payout state. An overdue self-employed
receipt blocks another request and another approval, but does not rewrite a paid
payout. Profile and payment details are copied into the request so later profile edits
cannot change an already approved register.

Every wallet command locks rows in one order: creator user, creator balance, payout
request. Accrual corrections use the same wallet lock order. PostgreSQL transaction
lock and statement timeouts bound contention, while deterministic ledger keys and
versioned command fingerprints make HTTP retries idempotent. A staff member cannot
process a payout whose blogger ID is their own user ID, even after a role change.

Payout events and their optional text/reference details are append-only. Once recorded,
approval, rejection, payment and receipt facts cannot be rewritten. A suspended blogger
can still read their own payout details and history, but only an active blogger can change
details or create a new request.

### Report exports

The API never generates report bytes. It validates a mandatory date range, records an
idempotent `export_job` and returns `202 Accepted`. The scheduler leases the row and sends
only `(export_id, dispatch_id)` to the dedicated export worker. That worker takes a bounded
database snapshot (at most 10,000 rows), generates CSV or XLSX directly from versioned
column definitions in code, calculates SHA-256 and uploads the artifact to a private
S3-compatible bucket. MinIO implements that interface locally; production may use managed
S3-compatible storage.

The API credential can only read artifacts; the worker credential can write and delete
them. Files expire after 24 hours, are never public, and downloads require a fresh staff
authorization check. CSV/XLSX use Moscow calendar dates, kopeck-safe money conversion and
formula-injection protection. The immutable job stores filename, size, row count, data
snapshot time and content hash; request and download actions are recorded in the security
journal.

### Account deletion and PII retention

A blogger starts deletion with a fresh password and an idempotency key. The API stores
only a SHA-256 confirmation-token digest and sends the raw 24-hour token through the
email outbox. Confirmation is a public token endpoint so a lost HTTP response can be
retried after all sessions have already been revoked. Redis limits token and IP attempts,
but PostgreSQL owns the request state and one-time transition.

Confirmation locks the creator and wallet, then rejects deletion while available or
reserved money, an active payout, a missing self-employed receipt or preliminary earnings
exist. A successful transaction marks the user/profile deleted, ends the collaboration,
inactivates publications, cancels provider/lifecycle jobs, revokes sessions and appends
deletion plus security history. No external email or queue call occurs inside that
transaction.

Payout PII is anonymized only after at least five years, only for terminal paid/rejected
requests belonging to a permanently deleted account, and only when no payout activity is
newer than the cutoff. The one-way transition replaces names, phone, bank and free text;
amounts, currency, statuses, dates, actor IDs, payment references, wallet ledger and event
facts remain intact. PostgreSQL triggers reject both arbitrary snapshot edits and attempts
to restore anonymized text.

Retention is a bounded one-shot maintenance process with its own database role. Production
orchestration invokes it daily; it is not an API endpoint and does not share worker secrets.
Each batch first anonymizes payout snapshots/details, then account identity, profile, social
links, support text, notification text and addressed outbox payloads. Stable UUIDs, consents,
money, publication/statistical facts and audit facts remain available. The canonical cutoff
is five years after the later relevant payout or `collaboration_ended_at`; missing lifecycle
dates fail closed and require an explicit data-reconciliation decision.

```powershell
docker compose --profile maintenance run --rm creator-retention
```

### Notifications and support

Domain services call one narrow `create_notification` port inside their existing
database transaction. A notification stores the rendered in-app text and the exact
template version; later template edits cannot rewrite history. Email delivery is a
separate outbox row containing a ready recipient, subject and body, so the email worker
remains unaware of profiles, publications, calculations, payouts or support tickets.
Recipient plus deduplication key makes repeated domain commands exactly-once at the
notification boundary.

Templates are versioned, append-only records. An administrator may deactivate the
current version by creating a new one. Rendering uses a fixed `string.Template`
variable allowlist, not executable template code. The notification archive is
owner-scoped, paginated and supports individual or bulk read markers.

Support tickets use the explicit workflow below; messages and transition events are
append-only while the ticket row is only the current projection:

```text
new -> in_progress -> waiting_blogger -> in_progress -> resolved -> closed
                    +-------------------------------> resolved
```

Manager/admin staff process ordinary tickets. Moderator/admin staff process account
recovery tickets. A suspended blogger can read history and create or reply to an
`account_recovery` ticket, but cannot use recovery as a second general-support queue.
Every write locks a freshly reloaded actor and then the ticket, uses a payload-bound
idempotency key, records security audit facts without copying message bodies, and
commits messages, status history, notifications and email outbox rows together.

### Account lifecycle

`creator_lifecycles` is the single inactivity projection. Only successful login,
publication changes, manual reading submission and payout request creation advance its
revision. The scheduler scans accounts in bounded hourly batches, creates version-bound
warning/transition jobs and sends only `(job_id, dispatch_id)` to the dedicated lifecycle
worker. A newer activity revision makes an already queued command obsolete.

After six calendar months without activity the worker atomically suspends the user and
profile, moves approved publications to `re_review_required`, cancels YouTube jobs and
writes notification plus security history. Read-only cabinet and payout history remain
available. After another six calendar months it blocks the account, revokes sessions and
records that the balance claim period expired. Warnings are sent in distinct 30-day and
7-day windows, so downtime cannot cause both warnings to be emitted together.

Recovery is an explicit moderator/admin decision on an `account_recovery` support ticket.
Approval starts a new activity period but does not reactivate old publications: each must
be resubmitted and moderated, which also creates a fresh YouTube baseline. Rejection leaves
the account suspended. The lifecycle worker has its own database role and no access to
payout requests or the balance ledger.

### Legal documents and acceptances

`legal_documents` stores immutable Markdown versions of program terms, personal-data
consent and the public privacy policy. Only one version of each type is current. Publishing
a replacement retires the previous version but never rewrites or deletes its text.
`legal_acceptances` stores the exact document ID, server timestamp and hashed request
context. A personal-data withdrawal only appends `withdrawn_at`; granting consent again
creates a new acceptance row.

Registration accepts explicit current IDs for program terms and personal-data consent.
The server locks and validates those versions and creates the user, both acceptances,
audit events and email outbox row in one transaction. Material replacements block only
new participation writes until accepted. Login, read-only history, support, deletion,
balance and payout operations remain available.

Migration `0024_legal_documents` preserves legacy consent facts, but marks their document
versions non-current because the previous schema did not store the original text. After
the migration an administrator must publish one real current version of all three document
types through `POST /api/v1/admin/legal-documents`. Registration and participation fail
closed with `LEGAL_DOCUMENTS_NOT_CONFIGURED` until that bootstrap is complete. Legal data
is API-only; scheduler and worker database roles receive no table privileges.

## Delivery and failure model

- Queue delivery is at least once.
- Commands are idempotent by database state plus `dispatch_id`.
- A broker publish failure releases the database lease immediately.
- A process crash leaves a bounded lease; scheduler recovery makes the job due again.
- One dispatcher failure is logged and cannot prevent other dispatchers from running.

## Database changes

Alembic is the only schema migration mechanism. Docker Compose runs the one-shot
`migrate` service before API, scheduler or workers start. Application processes never
create or modify tables at startup. The migration process loads only `DATABASE_URL`;
it does not depend on API, broker or provider settings.

The schema owner is a deployment credential, not an application credential. Only the
`migrate` service uses `DB_MIGRATION_USER`; API and every background process connect as
separate login roles provisioned by `docker/postgres/10-runtime-roles.sh`. The API has
ordinary table DML but owns no tables and has no `TRIGGER`, `TRUNCATE`, `REFERENCES` or
schema-creation rights. Each worker has grants only for the tables used by its own
command handler. In particular, email, YouTube, calculation and scheduler roles cannot
read payout requests, payout events, creator balances or the balance ledger.

Future tables are granted automatically only to `amp_api`. A migration that introduces
a table needed by a worker must explicitly grant the exact operations to that worker.
This makes a new cross-module database dependency visible in schema review.

Docker entrypoint scripts run only for an empty PostgreSQL data directory. After adding
these roles to an existing Docker volume, bootstrap them once before migration:

```powershell
docker compose exec postgres sh /docker-entrypoint-initdb.d/10-runtime-roles.sh
docker compose run --rm migrate
```

Migrations `0017_db_runtime_roles` and `0022_account_lifecycle` fail before applying grants when a role is missing,
elevated, a member of another role, or owns a database object. This is intentional: a
misconfigured deployment must not silently fall back to the schema-owner credential.
Compose credentials come from `.env` (start from `.env.example`); production secrets
must be unique, URL-safe values supplied by the deployment secret store.

## Dependency direction

```text
API routes -> domain services -> models/database
scheduler runner -> domain dispatchers -> contracts/models/database/broker
worker entrypoint -> one worker service -> contracts/models/database/provider client
```

Workers must not import API routes, authentication services, other workers or other
provider clients. Shared modules are limited to stable contracts, clock and database
factory code.
