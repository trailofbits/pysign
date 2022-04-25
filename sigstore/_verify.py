"""
API for verifying artifact signatures.
"""

import base64
import hashlib
from importlib import resources
from typing import Optional, TextIO, cast

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import (
    ExtendedKeyUsage,
    KeyUsage,
    RFC822Name,
    SubjectAlternativeName,
    load_pem_x509_certificate,
)
from cryptography.x509.oid import ExtendedKeyUsageOID
from OpenSSL.crypto import X509, X509Store, X509StoreContext

from sigstore._internal.merkle import (
    InvalidInclusionProofError,
    verify_merkle_inclusion,
)
from sigstore._internal.rekor import (
    RekorClient,
    RekorEntry,
    RekorInclusionProof,
)
from sigstore._internal.set import verify_set


# TODO(alex): Share this with `sign`
def _no_output(*a, **kw):
    pass


FULCIO_ROOT_CERT = resources.read_binary("sigstore._store", "fulcio.crt.pem")


def verify(
    file_: TextIO,
    certificate_path: TextIO,
    signature_path: TextIO,
    cert_email: Optional[str] = None,
    output=_no_output,
):
    """Public API for verifying blobs"""

    # Read the contents of the package to be verified
    output(f"Using payload from: {file_.name}")
    artifact_contents = file_.read().encode()
    sha256_artifact_hash = hashlib.sha256(artifact_contents).hexdigest()

    # Load the signing certificate
    output(f"Using certificate from: {certificate_path.name}")
    pem_data = certificate_path.read().encode()
    cert = load_pem_x509_certificate(pem_data)

    # Load the signature
    output(f"Using signature from: {signature_path.name}")
    b64_artifact_signature = signature_path.read().encode()
    artifact_signature = base64.b64decode(b64_artifact_signature)

    # In order to verify an artifact, we need to achieve the following:
    #
    # 1) Verify that the signing certificate is signed by the root certificate and that the signing
    #    certificate was valid at the time of signing.
    # 2) Verify that the signing certiticate belongs to the signer
    # 3) Verify that the signature was signed by the public key in the signing certificate
    #
    # And optionally, if we're performing verification online:
    #
    # 4) Verify the inclusion proof supplied by Rekor for this artifact
    # 5) Verify the Signed Entry Timestamp (SET) supplied by Rekor for this artifact

    # 1) Verify that the signing certificate is signed by the root certificate and that the signing
    #    certificate was valid at the time of signing.
    root = load_pem_x509_certificate(FULCIO_ROOT_CERT)

    sign_date = cert.not_valid_before
    openssl_cert = X509.from_cryptography(cert)
    openssl_root = X509.from_cryptography(root)

    store = X509Store()
    store.add_cert(openssl_root)
    store.set_time(sign_date)
    store_ctx = X509StoreContext(store, openssl_cert)
    store_ctx.verify_certificate()

    # 2) Check that the signing certificate contains the proof claim as the subject
    # Check usage is "digital signature"
    usage_ext = cert.extensions.get_extension_for_class(KeyUsage)
    if not usage_ext.value.digital_signature:
        # Error
        output("Key usage is not of type `digital signature`")
        return None

    # Check that extended usage contains "code signing"
    extended_usage_ext = cert.extensions.get_extension_for_class(ExtendedKeyUsage)
    if ExtendedKeyUsageOID.CODE_SIGNING not in extended_usage_ext.value:
        # Error
        output("Extended usage does not contain `code signing`")
        return None

    if cert_email is not None:
        # Check that SubjectAlternativeName contains signer identity
        san_ext = cert.extensions.get_extension_for_class(SubjectAlternativeName)
        print(san_ext.value.get_values_for_type(RFC822Name))
        if cert_email not in san_ext.value.get_values_for_type(RFC822Name):
            # Error
            output(f"Subject name does not contain identity: {cert_email}")
            return None

    output("Successfully verified signing certificate validity...")

    # 3) Verify that the signature was signed by the public key in the signing certificate
    signing_key = cert.public_key()
    signing_key = cast(ec.EllipticCurvePublicKey, signing_key)
    signing_key.verify(artifact_signature, artifact_contents, ec.ECDSA(hashes.SHA256()))

    output("Successfully verified signature...")

    # Get a base64 encoding of the signing key. We're going to use this in our Rekor query.
    pub_b64 = base64.b64encode(
        signing_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    # Retrieve the relevant Rekor entry to verify the inclusion proof and SET
    rekor = RekorClient()
    uuids = rekor.index.retrieve.post(sha256_artifact_hash, pub_b64.decode())

    valid_sig_exists = False
    for uuid in uuids:
        entry: RekorEntry = rekor.log.entries.get(uuid)

        # 4) Verify the inclusion proof supplied by Rekor for this artifact
        inclusion_proof = RekorInclusionProof.parse_obj(
            entry.verification.get("inclusionProof")
        )
        try:
            verify_merkle_inclusion(inclusion_proof, entry)
        except InvalidInclusionProofError as inval_inclusion_proof:
            output(
                f"Failed to validate Rekor entry's inclusion proof: {inval_inclusion_proof}"
            )
            continue

        # 5) Verify the Signed Entry Timestamp (SET) supplied by Rekor for this artifact
        verify_set(entry)

        valid_sig_exists = True

    if not valid_sig_exists:
        output("No valid Rekor entries were found")
        return None

    output("Successfully verified Rekor entry...")
    return None
