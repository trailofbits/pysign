"""
Utilities for verifying Signed Entry Timestamps.
"""

import base64
from importlib import resources
from typing import cast

import cryptography.hazmat.primitives.asymmetric.ec as ec
import jcs  # type: ignore
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from sigstore._internal.rekor import RekorEntry

REKOR_ROOT_PUBKEY = resources.read_binary("sigstore._store", "rekor.pub")


class InvalidSetError(Exception):
    pass


def verify_set(entry: RekorEntry) -> None:
    """Verify the Signed Entry Timestamp for a given Rekor entry"""

    # Put together the payload
    #
    # This involves removing any non-required fields (verification and attestation) and then
    # canonicalizing the remaining JSON in accordance with IETF's RFC 8785.
    raw_data = entry.raw_data.copy()
    del raw_data["verification"]
    del raw_data["attestation"]
    canon_data: bytes = jcs.canonicalize(raw_data)

    # Decode the SET field
    signed_entry_ts: bytes = base64.b64decode(
        entry.verification["signedEntryTimestamp"].encode()
    )

    # Load the Rekor public key
    rekor_key = load_pem_public_key(REKOR_ROOT_PUBKEY)
    rekor_key = cast(ec.EllipticCurvePublicKey, rekor_key)

    # Validate the SET
    try:
        rekor_key.verify(
            signature=signed_entry_ts,
            data=canon_data,
            signature_algorithm=ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as inval_sig:
        raise InvalidSetError from inval_sig
