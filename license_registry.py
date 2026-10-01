"""Transactional registration of signed licenses in the existing bot database."""
import time
from datetime import datetime, timezone

from quantix_licensing import LicenseSigner, normalize_hwid, inspect_revocation_update


def migrate(con):
    columns = {row[1] for row in con.execute("PRAGMA table_info(licenses)")}
    for name, definition in (("license_id", "TEXT"), ("customer_name", "TEXT"),
                             ("replaced_by", "TEXT"), ("deleted", "INTEGER NOT NULL DEFAULT 0"),
                             ("sync_version", "INTEGER NOT NULL DEFAULT 1")):
        if name not in columns:
            con.execute(f"ALTER TABLE licenses ADD COLUMN {name} {definition}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS licenses_id ON licenses(license_id)")
    con.execute("CREATE TABLE IF NOT EXISTS license_revisions(tier TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS imported_revocations(tier TEXT NOT NULL, license_id TEXT NOT NULL, PRIMARY KEY(tier,license_id))")
    con.execute("CREATE TABLE IF NOT EXISTS manager_sync_nonces(tier TEXT NOT NULL, nonce TEXT NOT NULL, issued INTEGER NOT NULL, PRIMARY KEY(tier,nonce))")


def normalize_key(value):
    compact = "".join(value.split())
    # Base64url in QTX2 is case-sensitive. Only old random codes were case-insensitive.
    return compact if compact.startswith("QTX2.") else compact.upper()


def lookup(con, reference):
    key = normalize_key(reference)
    return con.execute("SELECT * FROM licenses WHERE license_key=? OR license_id=?",
                       (key, reference.strip().lower())).fetchone()


def bump_revision(con, tier):
    old = con.execute("SELECT revision FROM license_revisions WHERE tier=?", (tier,)).fetchone()
    revision = max(time.time_ns() // 1_000_000, (old[0] + 1) if old else 1)
    con.execute("INSERT INTO license_revisions VALUES(?,?) ON CONFLICT(tier) DO UPDATE SET revision=excluded.revision",
                (tier, revision))
    return revision


def insert_license(con, tier, customer_name, customer_id, hwid, expires, actor_id, now=None):
    now = now or datetime.now(timezone.utc)
    signer = LicenseSigner.from_environment(tier)
    token, payload = signer.issue(customer_name, hwid, expires, now=now)
    con.execute("""INSERT INTO licenses(license_key,tier,hwid,discord_user_id,created_by,
                created_at,expires_at,license_id,customer_name) VALUES(?,?,?,?,?,?,?,?,?)""",
                (token, tier, payload["hwidHash"], customer_id, actor_id, payload["issuedAtUtc"],
                 payload.get("expiresAtUtc"), payload["licenseId"], payload["customer"]))
    bump_revision(con, tier)
    return lookup(con, token)


def replace_license(con, reference, hwid, expires, actor_id):
    """Caller owns the transaction. Retain old keys for audit/revocation exports."""
    row = lookup(con, reference)
    if row is None:
        raise ValueError("License not found.")
    if row["replaced_by"]:
        raise ValueError("This license has already been replaced. Use its replacement ID.")
    new = insert_license(con, row["tier"], row["customer_name"] or str(row["discord_user_id"]),
                         row["discord_user_id"], normalize_hwid(hwid), expires, actor_id)
    con.execute("UPDATE licenses SET revoked=1,revoked_at=?,revoked_by=?,replaced_by=?,sync_version=sync_version+1 WHERE license_key=?",
                (new["created_at"], actor_id, new["license_id"], row["license_key"]))
    return new


def revoke(con, reference, actor_id):
    row = lookup(con, reference)
    if row is None:
        raise ValueError("License not found.")
    con.execute("UPDATE licenses SET revoked=1,revoked_at=?,revoked_by=?,sync_version=sync_version+1 WHERE license_key=?",
                (datetime.now(timezone.utc).isoformat(), actor_id, row["license_key"]))
    bump_revision(con, row["tier"])
    return row


def export_update(con, tier):
    signer = LicenseSigner.from_environment(tier)
    ids = [row[0] for row in con.execute(
        "SELECT license_id FROM licenses WHERE tier=? AND revoked=1 AND license_id IS NOT NULL", (tier,))]
    ids.extend(row[0] for row in con.execute("SELECT license_id FROM imported_revocations WHERE tier=?", (tier,)))
    revision = bump_revision(con, tier)
    return signer.revocation_manifest(ids, revision)


def import_update(con, token, tier):
    data = inspect_revocation_update(token, tier)
    for license_id in data["RevokedIds"]:
        license_id = license_id.lower()
        con.execute("INSERT OR IGNORE INTO imported_revocations VALUES(?,?)", (tier, license_id))
        con.execute("UPDATE licenses SET revoked=1,revoked_at=?,sync_version=sync_version+1 WHERE tier=? AND lower(license_id)=? AND revoked=0",
                    (data["IssuedUtc"], tier, license_id))
    con.execute("INSERT INTO license_revisions VALUES(?,?) ON CONFLICT(tier) DO UPDATE SET revision=MAX(revision,excluded.revision)",
                (tier, data["Revision"]))
    return len(data["RevokedIds"])
