# Running a shared JobStager deployment

This is for whoever hosts JobStager for other people. People who use it never do any of
this. They just click **Sign in with Google**.

## Sign-in

A personal install signs in with a handle and password and needs no configuration.

A deployment that strangers share runs on Google instead, which is what lets it skip an
email service entirely -- there is no verification mail to send and no password to reset.
Register an OAuth client at <https://console.cloud.google.com/apis/credentials> (type
*Web application*), add the callback as an authorized redirect URI, and set:

| Variable | Meaning |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | From the console. Google sign-in is offered only when this and the secret are both set. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | From the console. |
| `JOBSTAGER_PUBLIC_URL` | The address users reach, e.g. `https://jobstager.example.com`. The callback is derived as `<url>/api/auth/google/callback`. |
| `GOOGLE_OAUTH_REDIRECT_URI` | Only if the callback is not at that path. Google matches it against the console entry exactly. |
| `JOBSTAGER_PASSWORD_AUTH=0` | Turns off handle-and-password sign-in, leaving Google as the only way in. |

Scopes are `openid email profile` -- all non-sensitive, so no Google verification review.
Requesting Sheets access is a separate, later consent, and deliberately not bundled here:
the `spreadsheets` scope *is* sensitive and would put the whole app behind a review.

An account that signs in with Google has no password at all. If someone already has a
password account, signing in with a Google address Google reports as **verified** links
the two rather than creating a duplicate; an unverified address never does.

## Letting strangers share one server

Set `JOBSTAGER_MULTI_TENANT=1`. It turns off the conveniences that only make sense on one
person's laptop: falling back to `profile.yaml`, seeding the first account from that file,
and `/api/stage`, which opens a browser on the machine running the server. With it set, the
server refuses to start without `JOBSTAGER_SECRET_KEY` (encrypts self-identification
answers) and `JOBSTAGER_SECURE_COOKIES=1`. Set `JOBSTAGER_ALLOWED_ORIGINS` to the public
address. Rate limits key on the caller's IP, which behind a proxy has to come from a
header the proxy writes: set `JOBSTAGER_CLIENT_IP_HEADER=True-Client-IP` on Render (it sits
behind Cloudflare, which overwrites that header), or `JOBSTAGER_TRUST_PROXY=1` elsewhere to
read the last `X-Forwarded-For` entry.

`DATABASE_URL` points the server at Postgres. Leave it unset for the local SQLite file.

## Resume storage

A shared server keeps resumes in a private Backblaze B2 bucket, because a free host's disk
is wiped on restart. Create a private bucket and an application key limited to that bucket
with read and write access, then set:

| Variable | Meaning |
|---|---|
| `B2_KEY_ID` | The key's keyID. |
| `B2_APPLICATION_KEY` | The key's secret, shown once when the key is created. |
| `B2_BUCKET_NAME` | The bucket. |

A shared deployment refuses to start without them. A personal install leaves them unset
and keeps resumes in `resumes/user_<id>/` on its own disk. Resumes uploaded before B2 was
turned on keep working from where they were stored.

## Google Sheets mirror (optional)

Mirroring applications to a Google Sheet currently uses a service-account key at
`~/.config/gcp/sheets-bot.json` and the sheet id in `JOBSTAGER_SPREADSHEET_ID` (or
`tracker.spreadsheet_id` in `profile.yaml`). Share the sheet with the service account's
address. Without these, the in-app Tracker works on its own.

## Scheduled crawl

`.github/workflows/crawl.yml` refreshes the job registry every six hours by running
`python3 -m cli.crawl` against the `DATABASE_URL` repository secret, the same Postgres the
server uses. It writes only public posting metadata. Run it by hand from the Actions tab
with **Run workflow**. GitHub pauses a schedule after 60 days without a commit to the
repository; re-enable it from the same tab.

