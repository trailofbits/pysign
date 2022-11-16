# Copyright 2022 The Sigstore Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
APIs for describing identity verification "policies", which describe how the identities
passed into an individual verification step are verified.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Callable, Type, TypeVar, cast

try:
    from typing import Protocol
except ImportError:  # pragma: no cover
    # TODO(ww): Remove when our minimum Python is 3.8.
    from typing_extensions import Protocol  # type: ignore[assignment]

from cryptography.x509 import (
    Certificate,
    ExtensionNotFound,
    ObjectIdentifier,
    OtherName,
    RFC822Name,
    SubjectAlternativeName,
    UniformResourceIdentifier,
)

from sigstore._verify.models import (
    VerificationFailure,
    VerificationResult,
    VerificationSuccess,
)

# From: https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md
_OIDC_ISSUER_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.1")
_OIDC_GITHUB_WORKFLOW_TRIGGER_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.2")
_OIDC_GITHUB_WORKFLOW_SHA_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.3")
_OIDC_GITHUB_WORKFLOW_NAME_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.4")
_OIDC_GITHUB_WORKFLOW_REPOSITORY_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.5")
_OIDC_GITHUB_WORKFLOW_REF_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.6")
_OTHERNAME_OID = ObjectIdentifier("1.3.6.1.4.1.57264.1.7")


_T = TypeVar("_T")


def _single_x509v3_extension(
    *, oid: ObjectIdentifier
) -> Callable[[type[_T]], type[_T]]:
    """
    A class-generating decorator for policies that only involve a single X.509v3
    extension's value.

    See `Issuer` and `GitHubWorkflowRef` for examples of use.
    """

    def decorator(cls: Type[_T]) -> Type[_T]:
        # NOTE(ww): mypy explicitly doesn't support decorator class chicanery.
        class Klass(cls):  # type: ignore[valid-type,misc]
            def __init__(self, value: str) -> None:
                self._value = value

            def verify(self, cert: Certificate) -> VerificationResult:
                try:
                    ext = cert.extensions.get_extension_for_oid(oid).value
                except ExtensionNotFound:
                    return VerificationFailure(
                        reason=(
                            f"Certificate does not contain {cls.__name__} "
                            f"({oid.dotted_string}) extension"
                        )
                    )

                # NOTE(ww): mypy is confused by the `Extension[ExtensionType]` returned
                # by `get_extension_for_oid` above.
                ext_value = ext.value.decode()  # type: ignore[attr-defined]
                if ext_value != self._value:
                    return VerificationFailure(
                        reason=(
                            f"Certificate's {cls.__name__} does not match "
                            f"(got {ext_value}, expected {self._value})"
                        )
                    )

                return VerificationSuccess()

        Klass.__name__ = cls.__name__
        Klass.__qualname__ = cls.__qualname__
        Klass.__doc__ = cls.__doc__
        return Klass

    return decorator


class VerificationPolicy(Protocol):
    @abstractmethod
    def verify(self, cert: Certificate) -> VerificationResult:
        raise NotImplementedError  # pragma: no cover


class AnyOf:
    """
    The "any of" policy, corresponding to a logical OR between child policies.

    An empty list of child policies is considered trivially invalid.
    """

    def __init__(self, children: list[VerificationPolicy]):
        self._children = children

    def verify(self, cert: Certificate) -> VerificationResult:
        verified = any(child.verify(cert) for child in self._children)
        if verified:
            return VerificationSuccess()
        else:
            return VerificationFailure(
                reason=f"0 of {len(self._children)} policies succeeded"
            )


class AllOf:
    """
    The "all of" policy, corresponding to a logical AND between child
    policies.

    An empty list of child policies is considered trivially invalid.
    """

    def __init__(self, children: list[VerificationPolicy]):
        self._children = children

    def verify(self, cert: Certificate) -> VerificationResult:
        # Without this, we'd consider empty lists of child policies trivially valid.
        # This is almost certainly not what the user wants and is a potential
        # source of API misuse, so we explicitly disallow it.
        if len(self._children) < 1:
            return VerificationFailure(reason="no child policies to verify")

        # NOTE(ww): We need the cast here because MyPy can't tell that
        # `VerificationResult.__bool__` is invariant with
        # `VerificationSuccess | VerificationFailure`.
        results = [child.verify(cert) for child in self._children]
        failures = [
            cast(VerificationFailure, result).reason for result in results if not result
        ]
        if len(failures) > 0:
            inner_reasons = ", ".join(failures)
            return VerificationFailure(
                reason=f"{len(failures)} of {len(self._children)} policies failed: {inner_reasons}"
            )
        return VerificationSuccess()


@_single_x509v3_extension(oid=_OIDC_ISSUER_OID)
class Issuer:
    """
    Verifies the certificate's OIDC issuer, identified by
    an X.509v3 extension tagged with `1.3.6.1.4.1.57264.1.1`.

    See: <https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md#1361415726411--issuer>
    """


class Identity:
    """
    Verifies the certificate's "identity", corresponding to the X.509v3 SAN.
    Identities are verified modulo an OIDC issuer, so the issuer's URI
    is also required.

    Supported SAN types include emails, URIs, and Sigstore-specific "other names".
    """

    def __init__(self, *, identity: str, issuer: str):
        self._identity = identity
        self._issuer = Issuer(issuer)

    def verify(self, cert: Certificate) -> VerificationResult:
        issuer_verified = self._issuer.verify(cert)
        if not issuer_verified:
            return issuer_verified

        san_ext = cert.extensions.get_extension_for_class(SubjectAlternativeName)
        verified = (
            self._identity in san_ext.value.get_values_for_type(RFC822Name)
            or self._identity
            in san_ext.value.get_values_for_type(UniformResourceIdentifier)
            or OtherName(_OTHERNAME_OID, self._identity.encode())
            in san_ext.value.get_values_for_type(OtherName)
        )

        if not verified:
            return VerificationFailure(
                reason=f"Certificate's SANs do not match {self._identity}"
            )

        return VerificationSuccess()


@_single_x509v3_extension(oid=_OIDC_GITHUB_WORKFLOW_REF_OID)
class GitHubWorkflowRef:
    ...
