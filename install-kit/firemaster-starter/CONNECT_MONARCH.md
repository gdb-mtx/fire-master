# Connect Monarch — switch from the demo to your own data

FIREMaster starts on a **demo persona** so you can explore safely. When you're ready to use your
**own finances**, connect your Monarch Money account. It's one command, and your data stays on your
machine — FIREMaster reads from Monarch and stores everything in your local database.

> **What this does:** after you connect and the first sync runs, the demo data is **replaced** by
> your real accounts automatically. The demo doesn't come back (you can always re-seed it later if
> you want a sandbox again). So connect when you actually want to go live.

**You'll need:** your Monarch email + password, and — if you have two-factor enabled — a code from
your **authenticator app**. Email MFA codes do NOT work here: the Monarch API only accepts
authenticator (TOTP) codes and can't trigger an email send. If your Monarch MFA is email-only,
first enable an authenticator app in Monarch (Settings → Security → Enable MFA), then come back.

---

## 1. Make sure the app is running

If you haven't already, from this folder:

```
docker compose up -d
```

## 2. Connect your Monarch account

Run this and answer the prompts (email, password, and MFA code if asked):

```
docker compose run --rm --no-deps -it backend uv run python ../scripts/monarch_login.py
```

Your password and MFA code are not echoed as you type. The session token lands, mode 0600, in
the private `monarch_session` Docker volume — that volume is your login, treat it like one.

- `-it` keeps it interactive so you can type your credentials.
- `--no-deps` runs just this step (it doesn't need the database).
- Your login is saved to the **`monarch_session`** volume — it persists across restarts and is
  shared with the background sync, so you only do this once (until the session eventually expires,
  then just run it again).

When it finishes you'll see `Session saved …` and `Verified: found N accounts`.

## 3. Pull your data

Open **http://localhost:5173**, go to the **Dashboard**, and click **Sync Now**. The first sync
imports your accounts, balances, and transactions, then clears the demo persona. (It also runs
automatically on a schedule, so even if you skip the button it'll happen on its own shortly.)

That's it — FIREMaster is now running on your real financial picture.

---

## Notes & troubleshooting

- **"Refusing to log in: DEMO_MODE is enabled"** — this instance is locked to demo-only. Make sure
  you did **not** set `DEMO_MODE=true` when starting it. (Default is off; you only get this if it was
  set deliberately, e.g. on a public demo box.)
- **Login fails / MFA loop** — re-run the command; have your authenticator code ready before you
  start (the codes rotate every 30s).
- **"MFA code" prompt but no code ever arrives** — your Monarch MFA is set to email codes, which
  the API can't trigger. Switch to an authenticator app in Monarch (Settings → Security), then
  re-run the login.
- **Session expired later** (sync stops importing new data) — just re-run the step-2 command to
  refresh it.
- **Nothing imported / `Sync: error` in the header** — open the logs (`docker compose logs
  celery-worker`) and look for the Monarch error; an expired session is the usual cause.
- **Going back to the demo** — connecting is one-way for the *data*, but you can re-seed a demo
  sandbox later if you want one. Ask support / see the docs.
