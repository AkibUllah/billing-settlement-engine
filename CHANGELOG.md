# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- CodeQL code scanning workflow and Dependabot configuration (pip, Docker, GitHub Actions).
- API reference and terminal screenshots in the README.
- Roadmap tracked as GitHub issues under the `v0.2.0` milestone.

### Changed

- CI workflow runs with a read-only `GITHUB_TOKEN` by default.
- Demo seed data uses a fictional card-acquiring bank (`DEMOBANK`, "Demo Card Bank").

### Fixed

- API views raise a DRF `DomainError` for service-layer errors instead of building responses from caught exceptions (CodeQL `py/stack-trace-exposure`); response bodies and status codes are unchanged.

## [0.1.0] - 2026-10-25

First public release.

### Added

- Double-entry ledger: accounts with normal balances, journal entries and postings; `post_journal` rejects unbalanced, negative, zero or mixed-currency entries and runs atomically; reversals and trial balance.
- Fee schedules: percentage, fixed, percentage + fixed and tiered (slab or marginal) with min/max caps and VAT (default 15 %), using half-up `Decimal` rounding.
- Transactions with partner commission and merchant fee frozen at ingestion; refunds net against gross.
- Merchant invoices per period with fee and VAT lines, unique numbering, an issue step that posts to the ledger, and text/CSV renderings.
- Settlement batches per partner per cutoff (gross − refunds − commission − VAT = net) with idempotent runs and a `PENDING → APPROVED → PAID` workflow that posts journals.
- Reconciliation against an uploaded partner CSV (matched, mismatched, missing on either side).
- `seed_demo` and `run_settlement` management commands.
- DRF API with filtering, search, pagination, token auth and an OpenAPI 3 schema; Django admin for every model.
- Health endpoint, whitenoise, gunicorn Dockerfile, docker compose with PostgreSQL 16, CI on Python 3.10/3.11, Pages API reference, Codespaces and Render kits.

### Fixed

- Platform hostnames are allowed on managed hosting.

[Unreleased]: https://github.com/AkibUllah/billing-settlement-engine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AkibUllah/billing-settlement-engine/releases/tag/v0.1.0
