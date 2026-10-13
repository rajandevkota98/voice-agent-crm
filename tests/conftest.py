import pytest

from voice_crm import crm
from voice_crm.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / "test.db"),
        public_base_url="https://example.test",
        whatsapp_token="wa-token",
        whatsapp_phone_id="12345",
        whatsapp_verify_token="verify-me",
        twilio_account_sid="AC123",
        twilio_auth_token="twilio-token",
        twilio_from_number="+15550001111",
    )


@pytest.fixture
def conn(settings):
    connection = crm.connect(settings.db_path)
    yield connection
    connection.close()
