"""Signed, hash-chained decision ledger (LG-1, LG-2, NF-3).

Each record carries prev_hash (hash of the previous record), its own hash over the canonical
JSON of everything else, and an ECDSA P-256 signature over that hash.

Standalone adapters: LocalSigner (key file, used only to sign) and FileLedgerStore (append-only
JSONL). AWS adapters with the same interfaces: KMS Sign (ECC_NIST_P256, ECDSA_SHA_256) and S3 with
Object Lock. The verifier needs only the records and the public key, so it runs offline.
"""

import base64
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64
UNSIGNED_FIELDS = ("hash", "signature")


class LedgerUnavailable(RuntimeError):
    pass


def canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k not in UNSIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(record: dict) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


# ---- signers ----------------------------------------------------------------------------
class Signer:
    key_id: str

    def sign_digest(self, digest: bytes) -> bytes:
        raise NotImplementedError

    def public_key_pem(self) -> bytes:
        raise NotImplementedError


class LocalSigner(Signer):
    """ECDSA P-256 key on disk. Stands in for a KMS key whose policy allows kms:Sign only."""

    def __init__(self, key_path: Path, pub_path: Path):
        key_path, pub_path = Path(key_path), Path(pub_path)
        if key_path.exists():
            self._key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        else:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            self._key = ec.generate_private_key(ec.SECP256R1())
            key_path.write_bytes(self._key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        pub = self.public_key_pem()
        pub_path.parent.mkdir(parents=True, exist_ok=True)
        if not pub_path.exists() or pub_path.read_bytes() != pub:
            pub_path.write_bytes(pub)
        self.key_id = "local:" + hashlib.sha256(pub).hexdigest()[:16]

    def sign_digest(self, digest: bytes) -> bytes:
        return self._key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))

    def public_key_pem(self) -> bytes:
        return self._key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


# ---- stores -----------------------------------------------------------------------------
class LedgerStore:
    def append(self, record: dict) -> None:
        raise NotImplementedError

    def read_all(self) -> List[dict]:
        raise NotImplementedError


class FileLedgerStore(LedgerStore):
    """Append-only JSONL. The application only ever opens it in append mode (no rewrite path)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.fault = False  # NF-1 test hook

    def append(self, record: dict) -> None:
        if self.fault:
            raise LedgerUnavailable("ledger store unreachable (fault injected)")
        line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def read_all(self) -> List[dict]:
        return read_records(self.path)


def read_records(path: Path) -> List[dict]:
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


# ---- ledger -----------------------------------------------------------------------------
class Ledger:
    def __init__(self, store: LedgerStore, signer: Signer):
        self.store, self.signer = store, signer
        self._lock = threading.Lock()
        existing = store.read_all()
        self.seq = existing[-1]["seq"] if existing else 0
        self.prev_hash = existing[-1]["hash"] if existing else GENESIS_HASH

    def write(self, **fields) -> dict:
        """Write one decision record. Raises LedgerUnavailable; callers must then fail closed."""
        with self._lock:
            rec = {"seq": self.seq + 1, "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                   **fields, "prev_hash": self.prev_hash, "key_id": self.signer.key_id}
            h = record_hash(rec)
            try:
                sig = self.signer.sign_digest(bytes.fromhex(h))
                rec["hash"], rec["signature"] = h, base64.b64encode(sig).decode()
                self.store.append(rec)
            except LedgerUnavailable:
                raise
            except Exception as e:
                raise LedgerUnavailable(f"ledger write failed: {e}") from e
            self.seq, self.prev_hash = rec["seq"], h
            return rec


# ---- offline verification -----------------------------------------------------------------
def verify_records(records: Iterable[dict], public_key_pem: bytes) -> Tuple[bool, List[str]]:
    """Returns (ok, lines). Checks sequence, hash chain, record hash and signature of every record."""
    pub = serialization.load_pem_public_key(public_key_pem)
    lines, ok, prev, expected_seq = [], True, GENESIS_HASH, 1
    for rec in records:
        seq = rec.get("seq")
        problems = []
        if seq != expected_seq:
            problems.append(f"sequence gap (expected {expected_seq})")
        if rec.get("prev_hash") != prev:
            problems.append("prev_hash does not match previous record")
        h = record_hash(rec)
        if h != rec.get("hash"):
            problems.append("content hash mismatch (record was modified)")
        try:
            pub.verify(base64.b64decode(rec.get("signature", "")), bytes.fromhex(h), ec.ECDSA(Prehashed(hashes.SHA256())))
        except (InvalidSignature, ValueError):
            problems.append("signature invalid")
        if problems:
            ok = False
            lines.append(f"FAIL seq={seq} rapp={rec.get('rapp_id')} decision={rec.get('decision')}: " + "; ".join(problems))
        prev = rec.get("hash", "")
        expected_seq = (seq or expected_seq) + 1
    lines.append(f"{'OK' if ok else 'FAIL'}: {expected_seq - 1} records checked")
    return ok, lines


def tamper_copy(src: Path, dst: Path, seq: Optional[int] = None) -> int:
    """Demo helper (D5): copy the ledger and alter one decision, as an attacker would."""
    recs = read_records(src)
    if not recs:
        raise ValueError("ledger is empty")
    target = next((r for r in recs if r["decision"] == "deny" and (seq is None or r["seq"] == seq)), recs[len(recs) // 2])
    target["decision"] = "allow"
    target["policy_id"] = None
    Path(dst).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs), encoding="utf-8")
    return target["seq"]
