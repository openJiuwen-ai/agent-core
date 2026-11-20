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

    def test_invalid_port_does_not_leak_password(self):
        url = "redis://:secret@host:abc/0"
        result = redact_url_password(url)
        assert result == "redis://:***@host:abc/0"
        assert "secret" not in result

    def test_out_of_range_port_does_not_leak_password(self):
        url = "redis://user:secret@host:99999999/db"
        result = redact_url_password(url)
        assert result == "redis://user:***@host:99999999/db"
        assert "secret" not in result

    def test_ipv6_host_keeps_brackets(self):
        url = "postgresql://u:secret@[::1]:5432/db"
        result = redact_url_password(url)
        assert result == "postgresql://u:***@[::1]:5432/db"
        assert "secret" not in result

    def test_password_with_at_sign(self):
        # Main path: ``netloc.rpartition("@")`` keeps the whole password, so the
        # '@' inside it must not truncate the redaction. Assert the full output,
        # not just the absence of a substring -- a partial leak would slip through.
        url = "redis://user:p@ss@host:6379/0"
        result = redact_url_password(url)
        assert result == "redis://user:***@host:6379/0"

    def test_password_with_at_sign_and_no_username(self):
        url = "redis://:p@ss@host:6379/0"
        result = redact_url_password(url)
        assert result == "redis://:***@host:6379/0"

    def test_unparseable_url_fallback_redacts_password_with_at_sign(self):
        # Malformed IPv6 makes ``urlparse`` raise, so this exercises the regex
        # fallback: the match must run to the last '@' or the password tail leaks.
        url = "redis://user:p@ss@[::1/db"
        result = redact_url_password(url)
        assert result == "redis://user:***@[::1/db"
        assert "ss@" not in result

    def test_unparseable_url_fallback_redacts_long_password_tail(self):
        url = "redis://user:abc@defghijklmnop@[::1/db"
        result = redact_url_password(url)
        assert result == "redis://user:***@[::1/db"
        assert "defghijklmnop" not in result

    def test_unparseable_url_fallback_redacts_password_without_username(self):
        url = "redis://:secret@[::1/db"
        result = redact_url_password(url)
        assert result == "redis://:***@[::1/db"
        assert "secret" not in result

    def test_unparseable_url_without_credentials_is_unchanged(self):
        url = "redis://[::1/db"
        assert redact_url_password(url) == url

    def test_fallback_leaves_query_at_sign_alone(self):
        # No password here: the '@' sits in the query. Redacting up to it would
        # destroy the authority and the query, so the fallback must not match.
        url = "redis://[::1?x=a@b"
        assert redact_url_password(url) == url
