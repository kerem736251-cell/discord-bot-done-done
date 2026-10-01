"""Authenticated two-way desktop manager sync, using the existing signing keys.

Requests use a separate QMS1 domain, bounded timestamp and one-use nonce. A signed
QTX2 license cannot authorize a sync. Server versions prevent stale restores.
"""
import re
import time
from datetime import datetime, timezone

from quantix_licensing import verify_envelope, inspect_license, product_name
import license_registry as registry


def synchronize(con, tier, token, now=None):
    now = int(time.time()) if now is None else now
    request = verify_envelope(token, "QMS1", tier, 16_000_000)
    if (request.get("version") != 1 or request.get("product") != product_name(tier) or
            type(request.get("timestamp")) is not int or abs(now - request["timestamp"]) > 120 or
            not isinstance(request.get("nonce"), str) or not re.fullmatch(r"[0-9a-f]{32}", request["nonce"]) or
            type(request.get("revision")) is not int or not 0 <= request["revision"] < 2**63 - 2 or
            not isinstance(request.get("changes"), list) or len(request["changes"]) > 10000):
        raise ValueError("Invalid synchronization request.")
    # Callers hold a BEGIN IMMEDIATE transaction, so replay checks and writes are atomic.
    con.execute("DELETE FROM manager_sync_nonces WHERE issued < ?", (now - 300,))
    if con.execute("SELECT 1 FROM manager_sync_nonces WHERE tier=? AND nonce=?", (tier, request["nonce"])).fetchone():
        raise ValueError("Synchronization request already processed. Retry with a new nonce.")
    con.execute("INSERT INTO manager_sync_nonces VALUES(?,?,?)", (tier, request["nonce"], request["timestamp"]))
    conflicts = []
    seen = set()
    for change in request["changes"]:
        if (not isinstance(change, dict) or not isinstance(change.get("token"), str) or
                type(change.get("version")) is not int or change["version"] < 0 or
                type(change.get("revoked")) is not bool or type(change.get("deleted")) is not bool):
            raise ValueError("Invalid synchronization change.")
        payload = inspect_license(change["token"], tier)
        license_id = payload["licenseId"]
        if license_id in seen:
            raise ValueError("Duplicate license in synchronization request.")
        seen.add(license_id)
        key = "".join(change["token"].split())
        row = registry.lookup(con, license_id)
        revoked, deleted = change["revoked"] or change["deleted"], change["deleted"]
        if row is None:
            if change["version"] != 0:
                # Server data was lost/rolled back: never resurrect a previously synced key.
                raise ValueError("Registry history is missing. Restore the server backup before syncing.")
            con.execute("""INSERT INTO licenses(license_key,tier,hwid,discord_user_id,created_by,created_at,
                        expires_at,revoked,license_id,customer_name,deleted) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (key, tier, payload["hwidHash"], 0, 0, payload["issuedAtUtc"], payload.get("expiresAtUtc"),
                         int(revoked), license_id, payload["customer"], int(deleted)))
            if con.execute("SELECT 1 FROM imported_revocations WHERE tier=? AND license_id=?", (tier, license_id.lower())).fetchone():
                con.execute("UPDATE licenses SET revoked=1 WHERE license_id=?", (license_id,))
        else:
            if row["tier"] != tier or row["license_key"] != key:
                raise ValueError("Conflicting immutable license data.")
            if change["version"] == 0:
                # First merge is a union; a stale desktop copy must not undo server revocations.
                revoked = revoked or bool(row["revoked"])
                deleted = deleted or bool(row["deleted"])
            elif change["version"] != row["sync_version"]:
                conflicts.append(license_id)
                continue
            if row["replaced_by"] and not revoked:
                conflicts.append(license_id)
                continue
            if bool(row["revoked"]) != revoked or bool(row["deleted"]) != deleted:
                con.execute("""UPDATE licenses SET revoked=?,deleted=?,revoked_at=?,sync_version=sync_version+1
                            WHERE license_id=?""", (int(revoked), int(deleted),
                            datetime.now(timezone.utc).isoformat() if revoked else None, license_id))
                # A version-checked explicit manager restore also clears an imported tombstone.
                if not revoked and change["version"] > 0:
                    con.execute("DELETE FROM imported_revocations WHERE tier=? AND license_id=?", (tier, license_id))
    con.execute("INSERT INTO license_revisions VALUES(?,?) ON CONFLICT(tier) DO UPDATE SET revision=MAX(revision,excluded.revision)",
                (tier, max(1, request["revision"])))
    revision = registry.bump_revision(con, tier)
    rows = con.execute("SELECT * FROM licenses WHERE tier=? AND license_id IS NOT NULL ORDER BY license_id", (tier,)).fetchall()
    return dict(Version=1, Product=product_name(tier), Revision=revision, Conflicts=conflicts,
                ExternalRevokedIds=[r[0] for r in con.execute("SELECT license_id FROM imported_revocations WHERE tier=?", (tier,))],
                Licenses=[dict(Id=r["license_id"], Customer=r["customer_name"] or str(r["discord_user_id"]),
                    Hwid=r["hwid"], Issued=r["created_at"], Expires=r["expires_at"], Token=r["license_key"],
                    Revoked=bool(r["revoked"]), Deleted=bool(r["deleted"]), SyncVersion=r["sync_version"],
                    SyncDirty=False, ReplacedBy=r["replaced_by"]) for r in rows])
