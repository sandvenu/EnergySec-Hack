"""AWS back ends for the framework, chosen by environment variables in framework/gateway.py.

    LEDGER_KMS_KEY_ID      KMS asymmetric key (ECC_NIST_P256, SIGN_VERIFY): signs every ledger record.
                           The host role may call kms:Sign only; the private key never leaves KMS.
    LEDGER_BUCKET          S3 bucket with Object Lock: one object per record (system of record);
                           the local JSONL file is a cache for the page and the verifier.
    KINESIS_STREAM         every decision and twin event is streamed (Firehose -> S3 -> Glue -> Athena).
    MODEL_REGISTRY_CHECK=1 admission requires an Approved model version in SageMaker Model Registry
                           for a card that names a model.

Nothing here changes a decision path: the signer and store keep the Signer / LedgerStore contracts
(a failure raises LedgerUnavailable, so the request fails closed); telemetry never blocks a decision.
"""

import json
import logging
import queue
import threading
from pathlib import Path
from typing import List, Optional

import boto3
from cryptography.hazmat.primitives import serialization

from framework.ledger import FileLedgerStore, LedgerStore, LedgerUnavailable, Signer

logger = logging.getLogger("aws")


class KmsSigner(Signer):
    """ECDSA P-256 over the record hash, in KMS (MessageType=DIGEST). Same signature format as LocalSigner
    (DER), so verify_ledger.py works unchanged with the public key KMS exports."""

    def __init__(self, key_id: str, pub_path: Optional[Path] = None, client=None):
        self.kms = client or boto3.client("kms")
        self.key = key_id
        meta = self.kms.describe_key(KeyId=key_id)["KeyMetadata"]
        self.key_id = "kms:" + meta["Arn"]
        der = self.kms.get_public_key(KeyId=key_id)["PublicKey"]
        self._pem = serialization.load_der_public_key(der).public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        if pub_path:
            Path(pub_path).parent.mkdir(parents=True, exist_ok=True)
            Path(pub_path).write_bytes(self._pem)

    def sign_digest(self, digest: bytes) -> bytes:
        return self.kms.sign(KeyId=self.key, Message=digest, MessageType="DIGEST",
                             SigningAlgorithm="ECDSA_SHA_256")["Signature"]

    def public_key_pem(self) -> bytes:
        return self._pem


class S3LedgerStore(LedgerStore):
    """Each record is written to S3 first (Object Lock retention from the bucket default), then to the
    local cache. A record that is not in S3 was not written: the request fails closed."""

    def __init__(self, bucket: str, local: FileLedgerStore, prefix: str = "ledger/records", client=None):
        self.s3 = client or boto3.client("s3")
        self.bucket, self.prefix, self.local = bucket, prefix.strip("/"), local
        self.fault = False
        if not self.local.read_all():
            self._restore()

    def _key(self, seq: int) -> str:
        return f"{self.prefix}/{seq:010d}.json"

    def _restore(self) -> None:
        """A fresh host continues the chain from S3 instead of starting a second chain."""
        keys = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix + "/"):
            keys += [o["Key"] for o in page.get("Contents", [])]
        for key in sorted(keys):
            rec = json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
            self.local.append(rec)
        if keys:
            logger.info("ledger restored from s3://%s/%s (%d records)", self.bucket, self.prefix, len(keys))

    def append(self, record: dict) -> None:
        if self.fault:
            raise LedgerUnavailable("ledger store unreachable (fault injected)")
        body = json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
        try:
            self.s3.put_object(Bucket=self.bucket, Key=self._key(record["seq"]), Body=body,
                               ContentType="application/json", ChecksumAlgorithm="SHA256")
        except Exception as e:
            raise LedgerUnavailable(f"S3 ledger write failed: {e}") from e
        self.local.append(record)

    def read_all(self) -> List[dict]:
        return self.local.read_all()


class KinesisSink:
    """Streams framework events (decisions, twin changes, approvals) for the data lake. Fire and forget:
    a bounded queue and one sender thread, so a slow or failing stream never delays a decision."""

    KINDS = {"decision", "twin", "approval", "screening", "event", "settings"}

    def __init__(self, stream: str, client=None):
        self.kinesis = client or boto3.client("kinesis")
        self.stream = stream
        self.q: "queue.Queue[dict]" = queue.Queue(maxsize=5000)
        threading.Thread(target=self._send, daemon=True).start()

    def __call__(self, event: dict) -> None:
        if event.get("type") not in self.KINDS:
            return
        try:
            self.q.put_nowait(event)
        except queue.Full:
            logger.warning("kinesis queue full; dropping a %s event", event.get("type"))

    def _send(self) -> None:
        while True:
            event = self.q.get()
            row = _flatten(event)
            try:
                self.kinesis.put_record(StreamName=self.stream, Data=(json.dumps(row, default=str) + "\n").encode(),
                                        PartitionKey=row.get("rapp_id") or row["kind"])
            except Exception as e:  # telemetry only
                logger.warning("kinesis put_record failed: %s", e)


def _flatten(event: dict) -> dict:
    """One row per event with the columns the Glue table declares; the full event is kept as JSON text."""
    kind = event["type"]
    rec = event.get("record") or {}
    row = {"kind": kind, "event_time": event.get("time"), "seq": rec.get("seq"), "record_time": rec.get("time"),
           "rapp_id": rec.get("rapp_id"), "decision": rec.get("decision"), "policy_id": rec.get("policy_id"),
           "tier": rec.get("tier"), "reason": rec.get("reason"), "justification": rec.get("justification"),
           "assessment_class": (rec.get("assessment") or {}).get("class"), "hash": rec.get("hash"),
           "prev_hash": rec.get("prev_hash"), "key_id": rec.get("key_id")}
    if kind == "twin":
        k = (event.get("result") or {}).get("kpis") or {}
        row.update({f"kpi_{x}": k.get(x) for x in ("weak_coverage_pct", "energy_saving_pct", "max_cell_load",
                                                    "excess_ues", "active_cells")})
    row["payload"] = json.dumps(event, default=str)[:60000]
    return row


class ModelRegistryCheck:
    """Admission gate: the card's model must have an Approved version in its SageMaker model package group."""

    def __init__(self, client=None):
        self.sm = client or boto3.client("sagemaker")

    def __call__(self, model: dict) -> Optional[str]:
        group = model["package_group"]
        try:
            res = self.sm.list_model_packages(ModelPackageGroupName=group, ModelApprovalStatus="Approved",
                                              SortBy="CreationTime", SortOrder="Descending", MaxResults=1)
        except Exception as e:  # fail closed: a model whose approval cannot be checked is not admitted
            return f"model registry unavailable for {group}: {e}"
        if not res.get("ModelPackageSummaryList"):
            return f"no Approved version of {group} in SageMaker Model Registry"
        return None
