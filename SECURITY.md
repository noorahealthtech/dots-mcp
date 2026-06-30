# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue for a
suspected vulnerability.

- Preferred: open a private security advisory via GitHub
  (**Security → Report a vulnerability** on this repository).
- Alternatively, email the Noora Health team: `sreeram@noorahealth.org`

We'll acknowledge your report and work with you on a fix and disclosure timeline.

## Secrets & credentials

This server talks to the Noora KMS API with a token, but **no secrets are committed
to this repo**:

- `.env` and `kms_schema.json` are gitignored — never commit real tokens, tenant IDs,
  or a populated schema cache.
- Configuration is environment-driven; copy `.env.example` to `.env` and fill it in
  locally. See the README for details.
- With no `KMS_AUTH_TOKEN`/`KMS_TENANT` set, the server runs in **mock mode** and uses
  only synthetic sample data, so it never needs live credentials to demo.

If you find a committed secret, please report it through the private channel above.
