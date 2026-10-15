from ilias_mcp.sanitize import clean_text, clean_tree, redact


def test_strips_ansi_zero_width_control():
    dirty = "A\x1b[31mB\x1b[0m\u200bC\x00D"
    clean, truncated = clean_text(dirty)
    assert clean == "ABCD"
    assert truncated is False


def test_defangs_markdown_and_truncates():
    dirty = '![x](https://evil.example/e.png) [go](https://evil.example)'
    clean, _ = clean_text(dirty)
    assert "![x]" not in clean
    assert "[Bild: x]" in clean
    assert "go (https://evil.example)" in clean
    long, truncated = clean_text("x" * 5000, limit=100)
    assert truncated and long.endswith(" …[gekürzt]") and len(long) <= 100


def test_redacts_secrets_and_query():
    tree = {"title": "token=SECRETTOKEN12 and more", "cookie": "abcSECRETTOKEN12xyz"}
    out = redact(tree, ["SECRETTOKEN12"])
    assert "SECRETTOKEN12" not in str(out)
    cleaned, _ = clean_text("https://x/?wstoken=ABCDEFGHIJKLMNOP&ok=1")
    assert "ABCDEFGHIJKLMNOP" not in cleaned
    assert "wstoken=[REDACTED]" in cleaned


def test_clean_tree_keys():
    out, truncated = clean_tree({"a\x1b[0m": ["ok", "\u200bx" * 3]})
    assert "a" in out and truncated is False
