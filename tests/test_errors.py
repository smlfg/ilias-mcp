from ilias_core.errors import CourseAmbiguousError, NotLoggedInError, SessionExpiredError

from ilias_mcp.errors import ToolFailure, from_core_error


def test_templates_never_forward_core_text():
    failure = from_core_error(NotLoggedInError("LEAK-CANARY-abc"), "hhn")
    payload = failure.payload()
    assert payload["code"] == "not_logged_in" and payload["exit_code"] == 2
    assert "LEAK" not in payload["message"] and "LEAK" not in payload["hint"]
    assert "ilias login --instance hhn" in payload["hint"]


def test_session_expired_and_ambiguous():
    assert from_core_error(SessionExpiredError("x"), "hhn").code == "session_expired"
    failure = from_core_error(
        CourseAmbiguousError("x", candidates=[{"id": 1, "fullname": "A\x1b[0m", "shortname": "a"}]),
        "hhn",
    )
    assert failure.code == "course_ambiguous"
    assert failure.payload()["candidates"][0]["fullname"] == "A"


def test_unknown_code_falls_back():
    class Weird(Exception):
        code = "weird"
        exit_code = 4

    assert from_core_error(Weird(), "").code == "network_error"
    assert ToolFailure("nope").code == "internal_error"
