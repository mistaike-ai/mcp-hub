from mcp_hub.auth import build_auth_headers
from mcp_hub.interfaces import Registration
import logging


class TestAuth:
    def test_none_auth_returns_empty_dict(self):
        reg = Registration(
            id="test_reg",
            user_id="user1",
            name="test_name",
            url="http://example.com",
            auth_type="none",
            log_mode="metadata",
        )
        headers = build_auth_headers(reg, None)
        assert headers == {}

    def test_api_key_auth_returns_bearer_header(self):
        reg = Registration(
            id="test_reg",
            user_id="user1",
            name="test_name",
            url="http://example.com",
            auth_type="api_key",
            log_mode="metadata",
        )
        headers = build_auth_headers(reg, "test_api_key")
        assert headers == {"Authorization": "Bearer test_api_key"}

    def test_api_key_auth_no_credential_returns_empty_dict_with_warning(self, caplog):
        reg = Registration(
            id="test_reg",
            user_id="user1",
            name="test_name",
            url="http://example.com",
            auth_type="api_key",
            log_mode="metadata",
        )
        with caplog.at_level(logging.WARNING):
            headers = build_auth_headers(reg, None)
            assert headers == {}
            assert "has auth_type='api_key' but no credential" in caplog.text

    def test_oauth_auth_returns_empty_dict_with_warning(self, caplog):
        reg = Registration(
            id="test_reg",
            user_id="user1",
            name="test_name",
            url="http://example.com",
            auth_type="oauth",
            log_mode="metadata",
        )
        with caplog.at_level(logging.WARNING):
            headers = build_auth_headers(reg, "oauth_token")
            assert headers == {}
            assert "auth_type='oauth' which is not yet supported" in caplog.text

    def test_unknown_auth_type_returns_empty_dict_with_warning(self, caplog):
        reg = Registration(
            id="test_reg",
            user_id="user1",
            name="test_name",
            url="http://example.com",
            auth_type="unknown",  # type: ignore
            log_mode="metadata",
        )
        with caplog.at_level(logging.WARNING):
            headers = build_auth_headers(reg, "some_credential")
            assert headers == {}
            assert "Unknown auth_type 'unknown'" in caplog.text
