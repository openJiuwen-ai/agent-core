from openjiuwen.core.common.utils.url_utils import redact_url_password


class TestRedactUrlPassword:
    def test_redact_password_with_username(self):
        url = "redis://user:secret@host:6379/0"
        result = redact_url_password(url)
        assert result == "redis://user:***@host:6379/0"
        assert "secret" not in result

    def test_redact_password_without_username(self):
        url = "redis://:secret@host:6379/0"
        result = redact_url_password(url)
        assert result == "redis://:***@host:6379/0"
        assert "secret" not in result

    def test_url_without_password(self):
        url = "redis://host:6379/0"
        result = redact_url_password(url)
        assert result == url

    def test_url_without_credentials(self):
        url = "redis://localhost:6379/1"
        result = redact_url_password(url)
        assert result == url

    def test_empty_url(self):
        assert redact_url_password("") == ""
        assert redact_url_password(None) is None

    def test_url_with_special_chars_in_password(self):
        url = "redis://:My%23SecretPwd@127.0.0.1:6379/0"
        result = redact_url_password(url)
        assert result == "redis://:***@127.0.0.1:6379/0"
        assert "My%23SecretPwd" not in result

    def test_mysql_url(self):
        url = "mysql://root:password123@localhost:3306/mydb"
        result = redact_url_password(url)
        assert result == "mysql://root:***@localhost:3306/mydb"

    def test_postgres_url(self):
        url = "postgresql://admin:secretpass@db.example.com:5432/production"
        result = redact_url_password(url)
        assert result == "postgresql://admin:***@db.example.com:5432/production"

    def test_url_without_port(self):
        url = "redis://:password@localhost/0"
        result = redact_url_password(url)
        assert result == "redis://:***@localhost/0"

    def test_invalid_url_returns_original(self):
        url = "not a valid url at all"
        result = redact_url_password(url)
        assert result == url

    def test_url_with_query_params(self):
        url = "redis://:secret@host:6379/0?ssl=true"
        result = redact_url_password(url)
        assert "secret" not in result
        assert "ssl=true" in result
