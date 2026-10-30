"""
Unit tests for CredentialManager and secure keyring storage.
"""

from unittest.mock import patch, MagicMock
from src.mail.credentials import CredentialManager


def test_credential_masking():
    assert CredentialManager.mask_secret(None) == "[NOT SET]"
    assert CredentialManager.mask_secret("") == "[NOT SET]"
    assert CredentialManager.mask_secret("pass") == "****"
    assert CredentialManager.mask_secret("secret12345") == "se****45"


def test_get_credentials_environment_fallback():
    with patch.dict("os.environ", {"IITD_EMAIL_USER": "testuser@iitd.ac.in", "IITD_EMAIL_PASSWORD": "envpassword123"}):
        user, pwd = CredentialManager.get_credentials()
        assert user == "testuser@iitd.ac.in"
        assert pwd == "envpassword123"


def test_keyring_retrieval_alias_resolution():
    fake_keyring_store = {
        ("IITD_OUTREACH_ENGINE", "ch1251635"): "vault_password_abc"
    }

    def mock_get_password(svc, user):
        return fake_keyring_store.get((svc, user))

    with patch.dict("os.environ", {"IITD_EMAIL_USER": "ch1251635@iitd.ac.in"}, clear=True):
        with patch("keyring.get_password", side_effect=mock_get_password):
            user, pwd = CredentialManager.get_credentials()
            assert user == "ch1251635"
            assert pwd == "vault_password_abc"


def test_set_and_delete_credentials():
    mock_keyring = MagicMock()
    with patch("keyring.set_password", mock_keyring.set_password), \
         patch("keyring.delete_password", mock_keyring.delete_password):

        assert CredentialManager.set_credentials("ch1251635", "secret_pass") is True
        # Should set for both ch1251635 and ch1251635@iitd.ac.in
        assert mock_keyring.set_password.call_count == 2

        assert CredentialManager.delete_credentials("ch1251635") is True
