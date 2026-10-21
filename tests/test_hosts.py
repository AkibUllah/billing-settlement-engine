from __future__ import annotations

from config.hosts import allowed_hosts, csrf_trusted_origins, split_list


def test_split_list_drops_blanks_and_whitespace() -> None:
    assert split_list("localhost, 127.0.0.1,,web ,") == ["localhost", "127.0.0.1", "web"]
    assert split_list("") == []


def test_render_hostname_is_appended_once() -> None:
    assert allowed_hosts(["localhost"], "billing.onrender.com") == [
        "localhost",
        "billing.onrender.com",
    ]
    assert allowed_hosts(["billing.onrender.com"], "billing.onrender.com") == [
        "billing.onrender.com"
    ]
    assert allowed_hosts(["localhost"], "") == ["localhost"]


def test_csrf_origins_include_https_render_origin() -> None:
    assert csrf_trusted_origins([], "billing.onrender.com") == ["https://billing.onrender.com"]
    assert csrf_trusted_origins(["https://a.example"], "") == ["https://a.example"]
