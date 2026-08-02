# Agent notes for the production deploy branch

This branch intentionally tracks the older production deployment. Keep changes
small and compatible with its FastAPI, SAQ, Postgres, Redis, and Compose setup.

If runtime behavior, configuration, APIs, database schema, or calculation logic
changes, update both `README.md` and this file.

## Analytics

- Umami is optional and configured by `UMAMI_ENABLED`, `UMAMI_HOST_URL`, and
  `UMAMI_WEBSITE_ID` in the server environment.
- Missing or incomplete configuration disables event delivery.
- Calculation requests must not depend on frontend analytics identifiers.
- `X-User-Agent` is optional and has a service fallback.
- Analytics network failures and non-success responses must not fail a
  calculation request.
