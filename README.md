<!-- topics: django,django-rest-framework,python,billing,settlement,double-entry-ledger,fintech,invoicing,postgresql -->
# Billing & Settlement Engine

A Django 4.2 + DRF core for billing merchants and settling with MFS and bank partners: a double-entry ledger with an enforced debits-equals-credits invariant, percentage/fixed/tiered fee schedules with VAT, invoice generation, per-partner settlement batches with a PENDING → APPROVED → PAID workflow, CSV reconciliation against partner reports, and merchant payout batches with a validated bank file export.

[![CI](https://github.com/AkibUllah/billing-settlement-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/AkibUllah/billing-settlement-engine/actions/workflows/ci.yml)
[![CodeQL](https://github.com/AkibUllah/billing-settlement-engine/actions/workflows/codeql.yml/badge.svg)](https://github.com/AkibUllah/billing-settlement-engine/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/AkibUllah/billing-settlement-engine)](https://github.com/AkibUllah/billing-settlement-engine/releases)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![Django](https://img.shields.io/badge/Django-4.2-092E20)
![DRF](https://img.shields.io/badge/DRF-3.14%2B-a30000)
![License](https://img.shields.io/badge/license-MIT-green)

## Try it live

| | |
|---|---|
| API reference | https://akibullah.github.io/billing-settlement-engine/ — Swagger UI built from the drf-spectacular schema (static; no backend behind it) |
| Run in your browser | [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/AkibUllah/billing-settlement-engine?quickstart=1) — starts web + PostgreSQL with `docker compose`, runs `seed_demo` and one settlement; open forwarded port 8000 at `/api/docs/` (authorize with the token from `.demo-credentials.txt`) or `/admin/` (`demo` / `demo12345`). |
| One-click deploy | [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/AkibUllah/billing-settlement-engine) — `render.yaml` provisions the web service and a free PostgreSQL; the container migrates and seeds on start and prints the API token to the logs. |
| Local | `docker compose up` (see Quick Start) |

## Screenshots

![Swagger UI API reference](docs/images/api-reference.png)

Live API reference on GitHub Pages, generated from the drf-spectacular schema.

![seed, settle and trial balance](docs/images/terminal-demo.png)

A real local run: `seed_demo` loads a week of transactions, `run_settlement --approve` nets them into one batch per partner, a re-run is a no-op, and the trial balance from the API still balances.

## Why this exists

When a platform collects payments through bKash, Nagad, Rocket and card acquirers on behalf of many merchants, three questions come up every month: how much does each partner owe us for the period (after their commission and VAT), how much do we owe each merchant (after our fees), and do the partner's statements agree with our records? I have seen this handled in spreadsheets more than once, with fee rules re-typed by hand and settlement amounts that could not be traced back to transactions. This project is the version I wanted: fees frozen on each transaction when it is recorded, every settlement and invoice backed by a balanced journal entry, and a reconciliation report that names the exact references that disagree.

## Features

- **Double-entry ledger** (`ledger`): accounts with normal balances, journal entries, postings; `post_journal` refuses unbalanced, negative, zero or mixed-currency entries and runs atomically; reversals; trial balance; per-partner receivable and per-merchant payable accounts created on demand
- **Fee schedules** (`billing`): percentage, fixed, percentage + fixed, and tiered (slab or marginal bands), with minimum/maximum caps and VAT (default 15 %); half-up `Decimal` rounding everywhere, no floats
- **Transactions** with partner commission and merchant fee frozen at ingestion; refunds carry no fees and net against gross
- **Invoices** per merchant per period: fee and VAT lines, unique numbering, issue step posts revenue + VAT output against the merchant payable; plain-text and CSV renderings
- **Settlement batches** (`settlement`) per partner per cutoff: gross − refunds − commission − VAT = net; idempotent runs; `PENDING → APPROVED → PAID` with invalid transitions rejected; approval and payment each post a journal
- **Reconciliation** from an uploaded partner CSV: matched, amount/type mismatches, missing-in-partner, missing-in-ledger, totals, and a status flag on the batch; closed once paid
- **Merchant payouts** (`payouts`): batches that pay each merchant's full payable balance, `draft → approved → exported → paid` with illegal transitions rejected, one balanced `Dr merchant payable / Cr bank` journal per line, a deterministic bank file validated before export, per-line paid/failed/returned outcomes with journal reversal, and a record of which settlements each line covers so none is paid twice
- **Management commands**: `seed_demo` (4 partners, 3 merchants, 7 fee schedules, a week of Bangladeshi-flavoured transactions, a demo API token), `run_settlement --date [--partner] [--approve]` and `run_payouts [--date] [--minimum] [--merchant] [--approve] [--export-dir]`
- **DRF API**: viewsets with django-filter, search, ordering and pagination; token auth; custom actions for quotes, invoice generation/issue, settlement run/approve/mark-paid/reconcile/export, payout run/approve/cancel/export/mark-paid and line outcomes; OpenAPI 3 schema via drf-spectacular with Swagger UI
- **Admin** registered for every model with inlines, filters and read-only financial fields
- Health endpoint, 12-factor settings, whitenoise static files, gunicorn Dockerfile, docker compose with PostgreSQL 16

## Architecture

```mermaid
flowchart TB
    subgraph Ingest
        T[Transaction<br/>partner_fee, merchant_fee frozen] 
    end
    subgraph Settlement["settlement app"]
        RUN[run_settlement cutoff] --> B[SettlementBatch<br/>gross − refunds − fee − VAT]
        B -- approve --> J1[Journal: Dr Partner Receivable, Dr Commission, Dr VAT Input / Cr Merchant Payables]
        B -- mark paid --> J2[Journal: Dr Bank / Cr Partner Receivable]
        CSV[partner report CSV] -- reconcile --> R[ReconciliationReport]
        R --> B
    end
    subgraph Billing["billing app"]
        FS[FeeSchedule<br/>pct · fixed · tiered · caps · VAT] --> T
        GEN[generate_invoice merchant, period] --> INV[Invoice + lines]
        INV -- issue --> J3[Journal: Dr Merchant Payable / Cr Fee Revenue, Cr VAT Output]
    end
    subgraph Payouts["payouts app"]
        PRUN[run payouts] --> PB[PayoutBatch + PayoutLines<br/>one line per merchant = full payable]
        PB -- approve --> J4[Journal per line: Dr Merchant Payable / Cr Bank]
        PB -- export --> FILE[DCN1 bank file<br/>validated, stored, SHA-256]
        FILE -- bank outcome --> OUT{paid · failed · returned}
        OUT -- failed or returned --> J5[Reversal of that line's journal<br/>settlements freed for a later batch]
    end
    subgraph Ledger["ledger app"]
        J1 & J2 & J3 & J4 & J5 --> L[(JournalEntry + Postings<br/>Σdebits == Σcredits)]
        L --> TB[Trial balance]
    end
    T --> RUN
    T --> GEN
    L -- merchant payable balances --> PRUN
    API[DRF API · admin · commands] --> Settlement & Billing & Payouts & Ledger
```

Design notes:

- **One write path into the ledger.** Nothing creates postings except `ledger.services.post_journal`, which validates the invariant before and after insert inside a transaction; a `CHECK (amount > 0)` constraint backs it at the database level.
- **Fees are historical facts.** A transaction stores the partner and merchant fee computed from the schedules in force when it was recorded, so repricing never changes settled or invoiced amounts.
- **Settlement is a snapshot with a state machine.** A batch locks its transactions (`status=settled`, `settlement_batch` set) so a re-run cannot double-count; transitions are a whitelist on the model and each step posts its own journal, which makes the ledger the audit trail.
- **Reconciliation is append-only.** Every upload produces a report; the batch carries the latest status, and paid batches refuse further uploads.
- **Payouts read the ledger, not a running total.** A line's amount is the merchant's payable balance at creation, so settlements credited and invoices deducted are both already in it.
- **A settlement is paid once.** Each line records the settlements it covers (`PayoutAllocation`), and a partial unique index allows one live allocation per settlement and merchant. Another index allows one unposted line per merchant, because a drafted amount is still in the balance. A failed, returned or cancelled line releases its allocations, so the money is paid again by exactly one later batch. Workflow steps lock the batch, line and merchant rows, and the races are tested on PostgreSQL.
- **Each line has its own journal,** so one bad account reverses without touching the rest of the batch. Approval refuses a stale batch (payable now below the line amount, merchant inactive, or bank details edited since drafting) and posts nothing; cancel it and draft a new one.
- **The bank is credited at approval,** as the funds are committed from then on; failed lines and returns put them back through `reverse_journal`. Payable balances include settlements that are approved but not yet remitted by the partner, so run payouts with that in mind if you do not want to fund merchants ahead of partner cash.
- **Money is `Decimal(18, 2)`** with `ROUND_HALF_UP` through one helper (`apps.common.money`); BDT formatting uses lakh/crore grouping.

## Tech Stack

| Layer | Choice |
| --- | --- |
| Framework | Django 4.2 LTS, Django REST Framework, django-filter, drf-spectacular |
| Database | SQLite (dev/tests), PostgreSQL 16 (compose) via dj-database-url + psycopg 3 |
| Auth | DRF TokenAuthentication (+ session for the browsable API/admin) |
| Serving | gunicorn, whitenoise |
| Tooling | pytest-django, ruff, Makefile, GitHub Actions (3.10/3.11, PostgreSQL 16 test job, migration check, docker build) |

## Quick Start

### Docker Compose (web + PostgreSQL)

```bash
git clone https://github.com/AkibUllah/billing-settlement-engine.git && cd billing-settlement-engine
cp .env.example .env
docker compose up --build -d
docker compose exec web python manage.py seed_demo        # prints admin login + API token
docker compose exec web python manage.py run_settlement   # yesterday's cutoff
open http://localhost:8000/api/docs/                        # Swagger UI
```

### Local (SQLite)

```bash
make install
make seed            # migrate + seed_demo
make dev             # http://127.0.0.1:8000/admin/  (demo / demo12345)
make settle DATE=2026-10-20
make test
```

Try the API with the token printed by `seed_demo`:

```bash
export TOKEN=...
curl -s -H "Authorization: Token $TOKEN" localhost:8000/api/settlements/batches/ | jq '.results[0]'
curl -s -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/settlements/batches/1/approve/
curl -s -H "Authorization: Token $TOKEN" -F file=@partner-report.csv localhost:8000/api/settlements/batches/1/reconcile/
curl -s -H "Authorization: Token $TOKEN" localhost:8000/api/ledger/trial-balance/
```

Pay merchants once settlements are approved:

```bash
make payout                                                  # draft a batch from payable balances
curl -s -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/payouts/batches/1/approve/
curl -s -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/payouts/batches/1/export/
curl -s -H "Authorization: Token $TOKEN" -o payout.csv localhost:8000/api/payouts/batches/1/bank-file/
curl -s -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' \
  -d '{"reason": "closed account"}' localhost:8000/api/payouts/lines/1/fail/
curl -s -H "Authorization: Token $TOKEN" -X POST localhost:8000/api/payouts/batches/1/mark-paid/
```

## API

Base path `/api/`. All routes except `health/` require `Authorization: Token <key>` (get one from `POST auth/token/`).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `auth/token/` | Obtain a token with username/password |
| `GET` | `health/` | Liveness (DB check) |
| CRUD | `partners/`, `merchants/` | Partners (MFS/bank/aggregator) and merchants with fee schedule links |
| CRUD | `fee-schedules/` | Schedules with nested tiers; `POST {id}/quote/ {amount}` previews fee + VAT |
| `GET/POST` | `transactions/` | Record payments/refunds (fees computed server-side); filters `merchant`, `partner`, `type`, `status`, `settlement_batch__isnull`, `occurred_at__gte/lte/date` |
| `GET` | `invoices/` | List/fetch; `POST generate/ {merchant, period_start, period_end}`; `POST {id}/issue/`; `GET {id}/text/`; `GET {id}/export/` (CSV) |
| `GET` | `settlements/batches/` | List/fetch; `POST run/ {cutoff_date, partners?}`; `POST {id}/approve/`; `POST {id}/mark-paid/ {payment_reference}`; `POST {id}/reconcile/` (multipart `file`); `GET {id}/export/` (CSV) |
| `GET` | `settlements/reconciliations/` | Reconciliation reports (filter `batch`, `is_clean`) |
| `GET` | `payouts/batches/` | List/fetch (detail includes lines and outcome totals); `POST run/ {value_date?, minimum_amount?, merchants?}`; `POST {id}/approve/`; `POST {id}/cancel/ {reason?}`; `POST {id}/export/`; `GET {id}/bank-file/`; `POST {id}/mark-paid/ {bank_reference?}` |
| `GET` | `payouts/lines/` | List/fetch (filter `batch`, `merchant`, `status`); `POST {id}/mark-paid/ {bank_reference?}`; `POST {id}/fail/ {reason}`; `POST {id}/return/ {reason}` |
| `GET` | `ledger/accounts/` | Accounts with live `balance`; `ledger/journals/` with postings; `ledger/trial-balance/` |
| `GET` | `schema/`, `docs/` | OpenAPI 3 JSON and Swagger UI |

Partner report CSV for reconciliation: header `external_ref,type,amount` (type `payment` or `refund`).

Illegal state transitions and stale payout batches answer `409`; other business-rule violations, including a payout run with nobody to pay, answer `400`.

### Bank file

Payouts export to DCN1, the format of the fictional Demo Clearing Network used here in place of a real bank's layout. Implement a real one by adding a renderer and parser next to `apps/payouts/bank_file.py`.

```
H,DCN1,DEMOPLATFORM,PO-20261108-000001,20261108,BDT
D,1,990000002,700100000002,Chittagong Traders & Co.,2000.00,PO-20261108-000001-0001
D,2,990000001,700100000001,Dhaka Mart Ltd.,700.50,PO-20261108-000001-0002
T,2,2700.50,1980000003
```

Records are header, entries (sequence, routing number, account number, account name, amount, reference) and a trailer with the count, the total and a routing hash (sum of routing numbers modulo 10^10). The file holds no timestamps, so a batch always renders to the same bytes. The originator comes from `PAYOUT_ORIGINATOR_ID`. Merchants need `bank_routing_number`, `bank_account_number` and `bank_account_name` set; a merchant without valid details is skipped and listed in the batch's `skipped` field.

## Project Structure

```
config/                 # settings (env-driven), urls, wsgi/asgi
apps/
├── common.py           # money(), percent_of(), format_bdt()
├── accounts/           # Partner, Merchant · API · admin · seed_demo command
├── ledger/             # Account, JournalEntry, Posting · post_journal/reverse/trial_balance · API
├── billing/            # FeeSchedule, FeeTier, Transaction, Invoice(Line) · fee calc, invoices · API
├── settlement/         # SettlementBatch, ReconciliationReport · batches, workflow, reconcile · run_settlement
└── payouts/            # PayoutBatch, PayoutLine, PayoutAllocation · workflow, DCN1 bank file · API · run_payouts
tests/                  # 165 tests (pytest-django; SQLite, plus 7 race tests on PostgreSQL)
Dockerfile · docker-compose.yml · Makefile · .github/workflows/ci.yml
```

## Testing

```bash
make test        # pytest-django against SQLite
make lint        # ruff + migration drift check
DATABASE_URL=postgres://billing:billing@127.0.0.1:5432/billing make test-pg   # adds the race tests
```

Covered: ledger invariants (unbalanced, negative, zero, duplicate reference, mixed currency, DB check constraint, tampering detection, reversal, trial balance); fee calculation for every schedule type including slab/marginal tiers, min/max caps, VAT and half-up rounding; transaction fee freezing and refunds; batch netting with refunds and cutoff filtering; per-partner grouping and idempotent re-runs; the full PENDING → APPROVED → PAID workflow with ledger balances checked after each step and invalid transitions rejected; CSV export; clean and discrepant reconciliation plus bad-file and closed-batch handling; invoice generation, issue postings, empty periods and renderings; both management commands; and the API end to end (token auth, schema, CRUD with filters, tiered schedule quotes, transaction immutability, settlement run/export/reconcile/approve/mark-paid, invoice generate/issue, trial balance); and payouts: amounts against ledger balances, journals and reversals, stale approval, the bank file byte for byte and against tampering, and concurrent runs, approvals, failures and returns on PostgreSQL.

## Roadmap

- [ ] Multi-currency settlement with FX journals (schema already carries currency per account/batch) (#1)
- [ ] Scheduled settlement runs (Celery beat or cron) and e-mailing invoices (#2)
- [ ] PDF invoices (reportlab) alongside the text/CSV renderings (#3)
- [ ] Row-level permissions so partner users see only their own batches (#5)

## License

MIT — see [LICENSE](LICENSE).
