"""Tests of the pure half of `io.opcua_security`.

No server: `discover_endpoints` is covered in
`tests/integration/test_opcua_security.py`, against the mock and nothing else.
What is here is the mapping between OPC UA's spellings and this project's, and
the one property that is a safety matter rather than a convenience — that a
`Credentials` object cannot print its own password.
"""

from __future__ import annotations

import pytest

from pssim.io.opcua_security import (
    POLICIES,
    POLICY_NAMES,
    POLICY_NONE,
    Credentials,
    EndpointOffer,
    SecurityMode,
    TokenType,
    policy_by_name,
    policy_name_for_uri,
)


class TestPolicies:
    def test_no_security_is_offered(self) -> None:
        assert POLICY_NONE in POLICY_NAMES

    def test_the_names_are_unique(self) -> None:
        assert len(set(POLICY_NAMES)) == len(POLICY_NAMES)

    def test_the_uris_are_unique(self) -> None:
        assert len({policy.uri for policy in POLICIES}) == len(POLICIES)

    def test_only_none_needs_no_certificate(self) -> None:
        without = [policy.name for policy in POLICIES if not policy.needs_certificate]

        assert without == [POLICY_NONE]

    def test_a_known_name_is_found(self) -> None:
        policy = policy_by_name("Basic256Sha256")

        assert policy is not None
        assert policy.uri.endswith("#Basic256Sha256")

    def test_an_unknown_name_is_not_an_error(self) -> None:
        # A settings file may name a policy this build cannot speak; defaulting
        # is a better answer than refusing to start.
        assert policy_by_name("Basic512Sha1024") is None


class TestPolicyUris:
    @pytest.mark.parametrize("policy", POLICIES, ids=lambda policy: policy.name)
    def test_every_uri_maps_back_to_its_name(self, policy: object) -> None:
        assert policy_name_for_uri(policy.uri) == policy.name  # type: ignore[attr-defined]

    def test_the_aes_uri_is_not_its_name(self) -> None:
        # `Aes128Sha256RsaOaep` is `#Aes128_Sha256_RsaOaep` on the wire — which
        # is why both are stated rather than one derived from the other.
        policy = policy_by_name("Aes128Sha256RsaOaep")

        assert policy is not None
        assert policy.uri.endswith("#Aes128_Sha256_RsaOaep")

    def test_an_unknown_uri_keeps_its_suffix(self) -> None:
        # A server may offer something newer than this build; showing its real
        # name is more use than hiding it.
        name = policy_name_for_uri("http://opcfoundation.org/UA/SecurityPolicy#Future1")

        assert name == "Future1"

    def test_a_uri_without_a_fragment_is_kept_whole(self) -> None:
        assert policy_name_for_uri("something-odd") == "something-odd"


def offer(
    policy_name: str = "Basic256Sha256",
    mode: SecurityMode = SecurityMode.SIGN_AND_ENCRYPT,
    tokens: tuple[TokenType, ...] = (TokenType.ANONYMOUS,),
    level: int = 3,
) -> EndpointOffer:
    return EndpointOffer(
        endpoint_url="opc.tcp://plc:4840/",
        policy_name=policy_name,
        mode=mode,
        security_level=level,
        token_types=tokens,
    )


class TestOffers:
    def test_it_reads_as_policy_and_mode(self) -> None:
        assert offer().label == "Basic256Sha256 / SignAndEncrypt"

    def test_a_secure_offer_needs_a_certificate(self) -> None:
        assert offer().needs_certificate is True

    def test_an_insecure_offer_does_not(self) -> None:
        assert offer(POLICY_NONE, SecurityMode.NONE).needs_certificate is False

    def test_it_reports_what_it_accepts(self) -> None:
        assert offer(tokens=(TokenType.USERNAME,)).accepts(TokenType.USERNAME) is True

    def test_it_reports_what_it_refuses(self) -> None:
        # This is the failure that used to arrive with no explanation: a server
        # that does not list Anonymous will refuse an anonymous session.
        assert offer(tokens=(TokenType.USERNAME,)).accepts(TokenType.ANONYMOUS) is False


class TestCredentials:
    def test_they_start_open_and_anonymous(self) -> None:
        credentials = Credentials()

        assert credentials.is_secure is False
        assert credentials.token is TokenType.ANONYMOUS

    def test_a_policy_makes_them_secure(self) -> None:
        credentials = Credentials(policy_name="Basic256Sha256", mode=SecurityMode.SIGN_AND_ENCRYPT)

        assert credentials.is_secure is True
        assert credentials.needs_certificate is True

    def test_the_description_names_the_user(self) -> None:
        credentials = Credentials(token=TokenType.USERNAME, username="operator")

        assert "operator" in credentials.describe()

    def test_the_description_says_anonymous_otherwise(self) -> None:
        assert "anonymous" in Credentials().describe()

    def test_the_description_never_holds_the_password(self) -> None:
        credentials = Credentials(token=TokenType.USERNAME, username="operator", password="s3cret")

        assert "s3cret" not in credentials.describe()

    def test_the_repr_never_holds_the_password(self) -> None:
        # A frozen dataclass prints every field, and this object ends up in log
        # lines and tracebacks.
        credentials = Credentials(token=TokenType.USERNAME, username="operator", password="s3cret")

        assert "s3cret" not in repr(credentials)

    def test_the_password_is_still_readable(self) -> None:
        # Hidden from a repr, not from the code that has to send it.
        assert Credentials(password="s3cret").password == "s3cret"
