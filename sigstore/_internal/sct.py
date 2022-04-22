"""
Utilities for verifying signed certificate timestamps.
"""

import struct

import cryptography.hazmat.primitives.asymmetric.ec as ec
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509 import Certificate

from sigstore._internal.fulcio import FulcioSignedCertificateTimestamp


def _pack_digitally_signed(
    sct: FulcioSignedCertificateTimestamp, cert: Certificate
) -> bytes:
    """
    The format of the digitally signed data is described in IETF's RFC 6962.

    1 SCT Version
    1 Signature Type
    8 Timestamp
    2 Entry Type
    3 Certificate Length
    X Certificate Data
    2 Extensions Length
    """

    # The digitally signed format requires the certificate in DER format.
    cert_der: bytes = cert.public_bytes(encoding=serialization.Encoding.DER)

    # The length should then be split into three bytes.
    unused, len1, len2, len3 = struct.unpack(
        "!4B",
        struct.pack("!I", len(cert_der)),
    )
    if unused:
        raise InvalidSctError(f"Unexpectedly large certificate length: {len(cert_der)}")

    # Assemble a format string with the certificate length baked in and then pack the digitally
    # signed data
    pattern = "!BBQhBBB%ssh" % len(cert_der)
    data = struct.pack(
        pattern,
        sct.struct["sct_version"],
        0,  # Signature Type
        sct.struct["timestamp"],
        0,  # Entry Type
        len1,
        len2,
        len3,
        cert_der,
        len(sct.struct["extensions"]),
    )

    return data


class InvalidSctError(Exception):
    pass


def verify_sct(
    sct: FulcioSignedCertificateTimestamp,
    cert: Certificate,
    ctfe_key: ec.EllipticCurvePublicKey,
) -> None:
    """Verify a signed certificate timestamp"""
    digitally_signed = _pack_digitally_signed(sct, cert)
    try:
        ctfe_key.verify(
            # The signature is prefixed with 2 bytes of data to describe the algorithms used to hash
            # and sign the data (described in IETF's RFC 5264) and 2 bytes containing the length of
            # the signature. So we should strip away the first 4 bytes before verifying.
            signature=sct.signature,
            data=digitally_signed,
            signature_algorithm=ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as inval_sig:
        raise InvalidSctError from inval_sig
