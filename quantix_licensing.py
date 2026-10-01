"""QTX2 wire format used by the existing Quantix Pro/Elite utilities.

Private keys are supplied at runtime; never generate replacement signing keys.
"""
import base64
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.exceptions import InvalidSignature


class LicenseConfigurationError(ValueError):
    pass


def normalize_hwid(value: str) -> str:
    value = value.strip()
    if value.upper().startswith("QX2-"):
        value = value[4:]
    value = value.replace("-", "").replace(" ", "").upper()
    if not re.fullmatch(r"[0-9A-F]{64}", value):
        raise ValueError("Paste the customer's full QX2 hardware ID (64 hex digits).")
    return value


def product_name(edition: str) -> str:
    product = edition if edition.startswith("quantix-") else "quantix-" + edition
    if product not in ("quantix-pro", "quantix-elite"):
        raise ValueError("Edition must be pro or elite.")
    return product


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("License timestamps must include a timezone.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def verify_envelope(token: str, prefix: str, edition: str, limit: int = 8192) -> dict:
    """Verify an edition-signed envelope without needing the private key."""
    try:
        product = product_name(edition)
        if len(token) > limit:
            raise ValueError()
        marker, body, signature = "".join(token.split()).split(".")
        if marker != prefix:
            raise ValueError()
        public = serialization.load_pem_public_key(
            (Path(__file__).parent / (product[8:] + "-signing-public.pem")).read_bytes())
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        width = (public.key_size + 7) // 8
        if len(raw) != width * 2:
            raise ValueError()
        der = utils.encode_dss_signature(int.from_bytes(raw[:width], "big"), int.from_bytes(raw[width:], "big"))
        public.verify(der, (prefix + "." + body).encode("ascii"), ec.ECDSA(hashes.SHA256()))
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature, OSError):
        raise ValueError("Invalid signed data or wrong edition.") from None


def inspect_license(token: str, edition: str) -> dict:
    data = verify_envelope(token, "QTX2", edition)
    try:
        if (data["version"] != 2 or data["product"] != product_name(edition) or
                not isinstance(data["customer"], str) or not data["customer"].strip() or
                not re.fullmatch(r"[0-9a-fA-F]{32}", data["licenseId"])):
            raise ValueError()
        data["hwidHash"] = normalize_hwid(data["hwidHash"])
        issued = datetime.fromisoformat(data["issuedAtUtc"])
        expires = datetime.fromisoformat(data["expiresAtUtc"]) if data.get("expiresAtUtc") else None
        if issued.tzinfo is None or (expires and (expires.tzinfo is None or expires <= issued)):
            raise ValueError()
        return data
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("Invalid signed license fields.") from None


def inspect_revocation_update(token: str, edition: str) -> dict:
    """Verify a desktop manager update before merging its revocation history."""
    product = product_name(edition)
    try:
        if len(token) > 2_000_000:
            raise ValueError()
        prefix, body, signature = "".join(token.split()).split(".")
        if prefix != "QRV2":
            raise ValueError()
        public = serialization.load_pem_public_key(
            (Path(__file__).parent / (product[8:] + "-signing-public.pem")).read_bytes())
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        width = (public.key_size + 7) // 8
        if len(raw) != width * 2:
            raise ValueError()
        der = utils.encode_dss_signature(int.from_bytes(raw[:width], "big"), int.from_bytes(raw[width:], "big"))
        public.verify(der, (prefix + "." + body).encode("ascii"), ec.ECDSA(hashes.SHA256()))
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        ids = data["RevokedIds"]
        if (data["Version"] != 1 or data["Product"] != product or
                type(data["Revision"]) is not int or not 1 <= data["Revision"] < 2**63 - 1 or
                not isinstance(ids, list) or len(ids) > 10000 or
                any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", x) for x in ids)):
            raise ValueError()
        issued = datetime.fromisoformat(data["IssuedUtc"])
        if issued.tzinfo is None or len(set(x.lower() for x in ids)) != len(ids):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature, OSError):
        raise ValueError("Invalid signed update, or wrong edition/signing key.") from None


class LicenseSigner:
    def __init__(self, edition: str, private_pem: bytes, public_pem: bytes):
        self.product = product_name(edition)
        try:
            self._key = serialization.load_pem_private_key(private_pem, password=None)
            public = serialization.load_pem_public_key(public_pem)
            if not isinstance(self._key, ec.EllipticCurvePrivateKey):
                raise ValueError()
            if not isinstance(public, ec.EllipticCurvePublicKey):
                raise ValueError()
            if self._key.public_key().public_numbers() != public.public_numbers():
                raise ValueError()
        except (ValueError, TypeError):
            raise LicenseConfigurationError("The signing key does not match this utility edition.") from None

    @classmethod
    def from_environment(cls, edition: str):
        product = product_name(edition)
        slug = product.removeprefix("quantix-")
        prefix = "QUANTIX_" + slug.upper() + "_PRIVATE_KEY"
        pem = os.getenv(prefix + "_PEM", "").replace("\\n", "\n")
        try:
            if not pem and os.getenv(prefix + "_FILE"):
                pem = Path(os.environ[prefix + "_FILE"]).read_text(encoding="utf-8")
            if not pem:
                raise LicenseConfigurationError("Missing " + prefix + "_PEM (or _FILE).")
            public = (Path(__file__).parent / (slug + "-signing-public.pem")).read_bytes()
            return cls(slug, pem.encode("utf-8"), public)
        except OSError:
            raise LicenseConfigurationError("Cannot read the configured license signing files.") from None

    def _sign(self, prefix: str, payload: dict) -> str:
        body = b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
        message = prefix + "." + body
        der = self._key.sign(message.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der)
        width = (self._key.key_size + 7) // 8
        return message + "." + b64(r.to_bytes(width, "big") + s.to_bytes(width, "big"))

    def issue(self, customer: str, hwid: str, expires: datetime | None,
              *, now: datetime | None = None, license_id: str | None = None) -> tuple[str, dict]:
        now = now or datetime.now(timezone.utc)
        if not customer.strip() or len(customer) > 256:
            raise ValueError("Customer must contain 1–256 characters.")
        if expires is not None and expires <= now:
            raise ValueError("Expiry must be in the future.")
        license_id = license_id or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-fA-F]{32}", license_id):
            raise ValueError("Invalid license ID.")
        payload = dict(version=2, product=self.product, licenseId=license_id,
                       customer=customer.strip(), hwidHash=normalize_hwid(hwid), issuedAtUtc=utc_text(now))
        if expires is not None:
            payload["expiresAtUtc"] = utc_text(expires)
        token = self._sign("QTX2", payload)
        if len(token) > 8192:
            raise ValueError("License exceeds the utility's size limit.")
        return token, payload

    def revocation_manifest(self, revoked_ids: list[str], revision: int) -> str:
        ids = sorted(set(x.lower() for x in revoked_ids))
        if revision < 1 or len(ids) > 10000 or any(not re.fullmatch(r"[0-9a-f]{32}", x) for x in ids):
            raise ValueError("Invalid revocation manifest.")
        return self._sign("QRV2", dict(Version=1, Product=self.product, Revision=revision,
                                      IssuedUtc=utc_text(datetime.now(timezone.utc)), RevokedIds=ids))
