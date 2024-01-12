# Copyright 2023 The Sigstore Authors
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

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from pydantic import BaseModel
from sigstore_protobuf_specs.dev.sigstore.common.v1 import HashAlgorithm


class Hashed(BaseModel):
    """
    Represents a hashed value.
    """

    algorithm: HashAlgorithm
    """
    The digest algorithm uses to compute the digest.
    """

    digest: bytes
    """
    The digest representing the hash value.
    """

    def as_prehashed(self) -> Prehashed:
        return Prehashed(self.hazmat_algorithm())

    def hazmat_algorithm(self) -> hashes.HashAlgorithm:
        if self.algorithm == HashAlgorithm.SHA2_256:
            return hashes.SHA256()
        # Add more hashes here.
        raise ValueError(f"unknown hash algorithm: {self.algorithm}")
