import json
from unittest.mock import AsyncMock

import pytest

from mcp_hub.interfaces import EncryptionProvider, Registration
from mcp_hub.logging import CallMetadata, LogSink, ZeroRetentionLogger


@pytest.fixture
def mock_log_sink():
    return AsyncMock(spec=LogSink)


@pytest.fixture
def mock_encryption_provider():
    return AsyncMock(spec=EncryptionProvider)


@pytest.fixture
def zero_retention_logger(mock_log_sink, mock_encryption_provider):
    return ZeroRetentionLogger(mock_log_sink, mock_encryption_provider)


class TestZeroRetentionLogger:

    USER_ID = "test_user"
    REGISTRATION_ID = "test_reg_id"
    TOOL_NAME = "test_tool"
    ARGUMENTS = {"param1": "value1"}
    RESPONSE = {"result": "success"}
    LATENCY_MS = 100
    STATUS = "success"
    USER_KEY = b"sixteen_byte_user_key"
    EXPIRES_AT = "2026-02-27T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_metadata_mode_does_not_pass_args_to_sink(
        self, zero_retention_logger, mock_log_sink, mock_encryption_provider
    ):
        reg = Registration(
            id=self.REGISTRATION_ID,
            user_id=self.USER_ID,
            name="test",
            url="http://test.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_log_sink.write_metadata.return_value = "log_entry_id_meta"

        await zero_retention_logger.log_call(
            registration=reg,
            tool_name=self.TOOL_NAME,
            arguments=self.ARGUMENTS,
            response=self.RESPONSE,
            latency_ms=self.LATENCY_MS,
            status=self.STATUS,
            user_key=self.USER_KEY,
            expires_at=self.EXPIRES_AT,
        )

        mock_log_sink.write_metadata.assert_called_once()
        args, _ = mock_log_sink.write_metadata.call_args
        metadata = args[0]
        assert isinstance(metadata, CallMetadata)
        assert metadata.registration_id == self.REGISTRATION_ID
        assert metadata.tool_name == self.TOOL_NAME
        assert metadata.latency_ms == self.LATENCY_MS
        assert metadata.status == self.STATUS
        assert metadata.request_size_bytes == len(json.dumps(self.ARGUMENTS).encode())
        assert metadata.response_size_bytes == len(json.dumps(self.RESPONSE).encode())

        mock_log_sink.write_encrypted_payload.assert_not_called()
        mock_encryption_provider.encrypt_payload.assert_not_called()

    @pytest.mark.asyncio
    async def test_encrypted_full_mode_encrypts_before_sink(
        self, zero_retention_logger, mock_log_sink, mock_encryption_provider
    ):
        reg = Registration(
            id=self.REGISTRATION_ID,
            user_id=self.USER_ID,
            name="test",
            url="http://test.com",
            auth_type="none",
            log_mode="encrypted_full",
        )
        mock_log_sink.write_metadata.return_value = "log_entry_id_full"
        mock_encryption_provider.encrypt_payload.return_value = {
            "ciphertext": b"encrypted_blob",
            "iv": b"iv",
            "auth_tag": b"auth_tag",
        }

        await zero_retention_logger.log_call(
            registration=reg,
            tool_name=self.TOOL_NAME,
            arguments=self.ARGUMENTS,
            response=self.RESPONSE,
            latency_ms=self.LATENCY_MS,
            status=self.STATUS,
            user_key=self.USER_KEY,
            expires_at=self.EXPIRES_AT,
        )

        mock_log_sink.write_metadata.assert_called_once()
        mock_encryption_provider.encrypt_payload.assert_called_once_with(
            {"arguments": self.ARGUMENTS, "response": self.RESPONSE}, self.USER_KEY
        )
        mock_log_sink.write_encrypted_payload.assert_called_once_with(
            "log_entry_id_full",
            {
                "ciphertext": b"encrypted_blob",
                "iv": b"iv",
                "auth_tag": b"auth_tag",
            },
        )

    def test_log_sink_is_abstract(self):
        msg = "Can't instantiate abstract class LogSink with abstract methods write_encrypted_payload, write_metadata"
        with pytest.raises(TypeError, match=msg):
            LogSink()

    @pytest.mark.asyncio
    async def test_log_call_with_none_response(
        self, zero_retention_logger, mock_log_sink, mock_encryption_provider
    ):
        reg = Registration(
            id=self.REGISTRATION_ID,
            user_id=self.USER_ID,
            name="test",
            url="http://test.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_log_sink.write_metadata.return_value = "log_entry_id_none_resp"

        await zero_retention_logger.log_call(
            registration=reg,
            tool_name=self.TOOL_NAME,
            arguments=self.ARGUMENTS,
            response=None,
            latency_ms=self.LATENCY_MS,
            status=self.STATUS,
            user_key=self.USER_KEY,
            expires_at=self.EXPIRES_AT,
        )

        mock_log_sink.write_metadata.assert_called_once()
        args, _ = mock_log_sink.write_metadata.call_args
        metadata = args[0]
        assert metadata.response_size_bytes == 0
