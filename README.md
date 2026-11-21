# family-notifier

Push notifications for a family to-do web app (Firebase Firestore + Cloud Messaging).
The app itself lives in a separate private repository; this repository holds only the notifier,
so it can use GitHub Actions for free. Nothing personal is stored here — names and the app
address come from a secret, and the logs show only counts.

## How it runs
`.github/workflows/notify.yml` is started from outside every ~5 minutes (e.g. cron-job.org calling
`POST /repos/<owner>/family-notifier/actions/workflows/notify.yml/dispatches` with `{"ref":"main"}`).

## Secrets (Settings → Secrets and variables → Actions)
- `FIREBASE_SA` — the Firebase service-account key (JSON).
- `FAMILY` — the family settings (JSON), for example:

```json
{
  "app_url": "https://example.pages.dev",
  "names": {"dad": "Dad", "mom": "Mom", "kid1": "Kid"},
  "female": ["mom"],
  "admins": ["dad", "mom"],
  "kids": ["kid1"],
  "groups": {"family": ["dad", "mom", "kid1"], "parents": ["dad", "mom"], "kids": ["kid1"]},
  "group_names": {"family": "Family", "parents": "Parents", "kids": "Kids"}
}
```
