# Security Policy

FIREMaster is self-hosted software that holds financial credentials (a Monarch Money session)
and financial data. Security reports are taken seriously and read by the author personally.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report privately via **GitHub's private vulnerability reporting** (Security tab → "Report a
vulnerability" on this repo), or email **gdborshukov@gmail.com** with `[FIREMaster security]`
in the subject.

Include what you can: affected component, reproduction steps, and impact as you understand
it. You'll get a human reply — typically within a few days, no SLA — and credit in the fix
commit if you want it.

## Scope notes for researchers

- The threat model assumes a **single-user deployment on localhost or a private network**.
  The app has one admin user and is not designed to be exposed to the public internet;
  reports assuming a hardened multi-tenant deployment are out of scope.
- The standard Compose files bind published services to `127.0.0.1`. The standalone install
  keeps PostgreSQL and Redis entirely inside the Compose network. Changing those bindings to
  `0.0.0.0`, or placing the app behind a public proxy without additional hardening, is unsupported.
- **https://demo.firemaster.io is a shared sandbox with synthetic data** that resets every
  two hours. It is in scope for responsible testing (please don't DoS it), and nothing on
  it is secret — including the demo password.
- Secrets live in `backend/.env` (gitignored) and are generated per-install by
  `app.setup`. If you find a way for any real secret or PII to reach the repo, the image,
  or the logs, that is exactly the class of bug to report.

## Supported versions

The latest commit on `main` (and the GHCR images built from it) is the only supported
version.
