from __future__ import annotations

import ipaddress

import pytest

from job_platform.errors import JobValidationError
from web_collector.browser.url_policy import UrlPolicy
from web_collector.markdown import html_to_markdown


def resolver_for(*addresses):
    return lambda _host, _port: addresses


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "https://example.com:8443/",
        "https://example.com/#secret",
        "https://example.com/\nheader",
        "https:\\example.com",
    ],
)
def test_url_policy_rejects_malformed_or_unsafe_urls(url):
    with pytest.raises(JobValidationError):
        UrlPolicy(resolver_for("93.184.216.34")).validate(url)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0", "::1", "fe80::1", "fc00::1"],
)
def test_url_policy_rejects_every_non_global_dns_answer(address):
    assert not ipaddress.ip_address(address).is_global
    with pytest.raises(JobValidationError, match="not publicly routable"):
        UrlPolicy(resolver_for("93.184.216.34", address)).validate("https://example.com/path")


def test_url_policy_returns_canonical_public_target_without_leaking_query_in_errors():
    result = UrlPolicy(resolver_for("2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34")).validate(
        "HTTPS://Example.COM./path?q=secret"
    )
    assert result.url == "https://example.com/path?q=secret"
    assert result.port == 443
    with pytest.raises(JobValidationError) as captured:
        UrlPolicy(resolver_for("127.0.0.1")).validate("https://example.com/path?q=secret")
    assert "secret" not in str(captured.value)


def test_markdownify_golden_preserves_structure_and_removes_active_content():
    html = """
      <article><h1>공지</h1><script>alert(1)</script>
      <p>새 소식과 <a href="/details">상세 내용</a> <a href="javascript:alert(1)">위험</a></p>
      <ul><li>첫째</li><li>둘째</li></ul>
      <table><thead><tr><th>이름</th><th>값</th></tr></thead><tbody><tr><td>A</td><td>1</td></tr></tbody></table>
      <pre><code>print(&quot;ok&quot;)</code></pre><img src="private.png" alt="도표">
      </article>
    """
    markdown = html_to_markdown(html, final_url="https://example.com/base/")
    assert "# 공지" in markdown
    assert "[상세 내용](https://example.com/details)" in markdown
    assert "javascript:" not in markdown
    assert "alert(1)" not in markdown
    assert "- 첫째" in markdown
    assert "| 이름 | 값 |" in markdown
    assert 'print("ok")' in markdown
    assert "[도표]" in markdown
    assert "private.png" not in markdown
