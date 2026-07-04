# Lumin India — Owner's Manual

Everything you do by hand, in one place. If it's not in here, the system
does it itself. Last updated: 2026-07-04.

---

## The ONE daily ritual (trading mornings, before 09:15 IST)

SEBI requires a human 2FA login on every broker API, every day. Fyers
makes that a single tap:

1. Open this bookmark on your phone (save it now):

   ```
   https://api-t1.fyers.in/api/v3/generate-authcode?client_id=QHX93US4FU-100&redirect_uri=https%3A%2F%2Flumintrade.app%2Ffyers%2Fcallback&response_type=code&state=lumin
   ```

2. Log in to Fyers (fingerprint / TOTP).
3. You land on a page saying **"✓ Token refreshed — Engine is connected
   to live NSE data."** Close it. Done — the engine hot-swapped the feed,
   no restart, nothing else to do.

Forgot until mid-day? Same tap fixes it whenever you do it. Weekends and
NSE holidays: skip entirely, the engine sleeps on its own.

**Want zero taps?** Do the Angel One setup (below, one-time) and the
engine logs itself in every morning.

---

## One-time setups still pending (each unlocks something)

### A. App on your phone (unlocks: seeing signals on the device)
1. GitHub → `Lumin-india-app` → Settings → Secrets and variables →
   Actions → New repository secret:
   **`INDIA_API_TOKEN`** = the same value as the engine's
   `API_STATIC_TOKEN` secret.
2. Actions tab → **Build testing APK** → Run workflow.
3. When green: open the run → download `lumin-india-testing-apk` →
   unzip → install `app-release.apk` (allow unknown sources).
4. Expect: session bar shows "Market Closed" on a weekend — not an
   error. If it says **"Access denied"**, the secret is missing/wrong.

### B. Angel One zero-touch data feed (unlocks: no daily tap, ever)
1. Open a free Angel One account (angelone.in).
2. `smartapi.angelbroking.com` → Create an app → note the **API key**.
3. `smartapi.angelbroking.com/enable-totp` → enable TOTP → **save the
   base32 secret string it shows** (you'll never see it again).
4. GitHub → `Lumina-india-engine` → Actions secrets, add all five:
   `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE` (your client code),
   `ANGEL_PIN` (trading PIN), `ANGEL_TOTP_SECRET` (step 3),
   `DATA_FEED` = `angel`.
5. Re-run the Deploy workflow (or push anything to main). From the next
   morning the engine authenticates itself at 08:40 IST daily.

### C. Firebase project (unlocks: app login + push notifications)
1. console.firebase.google.com → Add project → name it "Lumin India"
   (separate from the crypto app's project).
2. Authentication → Sign-in method → enable **Phone**.
3. Project settings → Add app → **Android**, package name
   `org.luminapp.lumin_india` → download `google-services.json` — send
   it into the build (CTE will wire it when you say it exists).
4. Project settings → Service accounts → Generate new private key →
   add the JSON **contents** as engine repo secret
   `FIREBASE_SERVICE_ACCOUNT`.

### D. Cloudflare Full (strict) TLS (do before real subscribers)
1. Cloudflare → lumintrade.app → SSL/TLS → Origin Server → Create
   Certificate (defaults are fine) → keep the cert + key shown.
2. On the VPS run `bash scripts/vps_bootstrap.sh` and paste them when
   prompted (it configures nginx :443).
3. Cloudflare → SSL/TLS → Overview → switch mode Flexible → **Full
   (strict)**.

### E. Razorpay account (unlocks: subscriptions — needed at launch, not now)

---

## Checking on the system (any time, from the phone browser or Termux)

Replace `$TOKEN` with your `API_STATIC_TOKEN` value.

| What | How |
|---|---|
| Is it alive? | `https://lumintrade.app/api/health` (no token needed) |
| Engine state | `curl -H "Authorization: Bearer $TOKEN" https://lumintrade.app/api/pulse` |
| Today's signals | `.../api/signals?limit=20` (same header) |
| Why signals were blocked | `.../api/suppressed` |
| Signal outcomes (TP/SL/expired + points) | `.../api/outcomes` |
| Daily ledger — the 30-day quality window | `.../api/session-summary` |

On the VPS (Termux → `ssh root@95.111.241.97`):

```bash
docker logs india-engine --tail 50     # engine logs
docker ps                              # both containers Up?
```

---

## When something looks wrong

| Symptom | Meaning | Fix |
|---|---|---|
| App: "Access denied" | APK built without/with wrong token | Setup A above |
| App: "Engine unreachable" | Network or engine down | Check `/api/health`; if down → VPS `docker ps` + logs |
| Engine log: "token exchange failed" after your morning tap | Auth code expired (rare) | Just tap the bookmark again |
| `/api/pulse` shows `session_state: OPEN` but `scan_count` frozen | Scan loop wedged | `docker restart india-engine` (engine resumes + re-seeds itself) |
| Cloudflare 521 page | Nginx/VPS down | VPS: `systemctl status nginx`, `docker ps` |
| No signals for days | Normal-ish (strict gates) — but check `/api/suppressed`; if THAT is also empty during market hours, ask CTE to investigate | |

**Everything deploys itself:** any merge to `main` reaches the VPS in
~3 minutes. You never SSH to deploy.

---

## What the system does WITHOUT you

- Scans NIFTY + BANKNIFTY every 30s, 09:15–15:30 IST, Mon–Fri,
  NSE holidays excluded (2026 calendar verified from the NSE circular)
- Stores every signal, every gate suppression, every TP1/SL/expiry
  outcome, and writes the daily session-summary ledger at close
- Restarts cleanly: containers auto-restart, tracking resumes, the
  freshest token survives
- CI + auto-deploy on every merge
