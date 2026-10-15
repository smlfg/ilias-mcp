from ilias_mcp.core import apply_request_budget, prune


def test_prune_caps_nodes():
    tree = [{"name": "s", "modules": [{"name": f"m{i}", "children": [{"name": "c"}]} for i in range(10)]}]
    kept, count, truncated = prune(tree, 5)
    assert count == 5 and truncated is True
    kept, count, truncated = prune(tree, 1000)
    assert count == 21 and truncated is False


def test_request_budget_capped(monkeypatch):
    import os

    monkeypatch.setenv("ILIAS_CLI_MAX_REQUESTS", "999")
    apply_request_budget()
    assert os.environ["ILIAS_CLI_MAX_REQUESTS"] == "60"
    monkeypatch.setenv("ILIAS_CLI_MAX_REQUESTS", "10")
    apply_request_budget()
    assert os.environ["ILIAS_CLI_MAX_REQUESTS"] == "10"
