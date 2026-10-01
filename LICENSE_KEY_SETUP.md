# Quantix V2.4 — License Key Setup

## What V2.4 adds

Staff can manage Utility licenses directly from Discord:

- `/key create` — choose customer, Pro/Elite, HWID, and duration
- `/key info` — inspect status, tier, HWID and expiry
- `/key revoke` — revoke immediately
- `/key extend` — add time or make permanent
- `/key reset-hwid` — replace the bound HWID
- `/keys user` — list a customer's licenses

Durations: 1 day, 1 week, 1 month, 1 year, permanent.

The generated key is sent to the selected customer by Discord DM. The full key is intentionally not written to public logs.

## Important: persist the database on Railway

The key database is SQLite. Railway's normal container filesystem can be replaced during redeploys, so mount a Railway Volume before using this for real customers.

1. Open the Bot service in Railway.
2. Add a Volume mounted at `/data`.
3. Add/update the Railway variable:

   `DB_PATH=/data/quantix.db`

4. Redeploy once.

Existing Discord variables stay in the same service.

## Utility validation API

V2.4 also starts an HTTP API in the same Railway service.

Generate a public Railway domain for the service. If Railway gives you a domain like:

`https://your-service.up.railway.app`

then the Utility validates a license with:

`POST https://your-service.up.railway.app/api/license/validate`

JSON body:

```json
{
  "key": "QTX-ELITE-ABC123-DEF456",
  "hwid": "CUSTOMER-HWID-HERE"
}
```

Successful response example:

```json
{
  "ok": true,
  "status": "active",
  "tier": "elite",
  "expires_at": "2026-11-01T12:00:00+00:00",
  "permanent": false,
  "discord_user_id": "123456789"
}
```

Possible rejected statuses include `invalid`, `revoked`, `expired`, and `hwid_mismatch`.

Health check:

`GET /health`

## Staff permissions

The key commands are allowed for:

- Server administrators / members with Manage Server
- Members with the configured Quantix Support role

## Connecting the actual Utility

The backend/API is included in this version, but the Pro/Elite Utility itself still needs to be updated to call `/api/license/validate` during login/activation. Until the Utility code is updated, creating a Discord key will store a valid license in this backend but an older Utility build will not know to check it.
