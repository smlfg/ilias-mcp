"""Tool calls against the localhost fake ILIAS (HHN-like instance)."""

from __future__ import annotations

import pytest

from .fake_ilias import FakeIlias
from .helpers import call


def test_list_instances_offline(fake: FakeIlias):
    r = call("ilias_list_instances")
    assert not r.is_error
    keys = {i["key"] for i in r.structured_content["instances"]}
    assert {"hhn", "uni-mannheim", "hs-mannheim"} <= keys
    assert r.structured_content["default_instance"] == "hhn"
    assert fake.requests == []  # no network


def test_list_instances_shows_session_file(logged_in: FakeIlias):
    r = call("ilias_list_instances")
    hhn = next(i for i in r.structured_content["instances"] if i["key"] == "hhn")
    assert hhn["session_file_present"] is True
    assert hhn["base_url"] == logged_in.base


def test_get_status_ok(logged_in: FakeIlias):
    r = call("ilias_get_status", {"instance": "hhn"})
    assert not r.is_error, r.content
    assert r.structured_content["authenticated"] is True
    assert len(logged_in.requests) == 1


def test_list_courses_one_request(logged_in: FakeIlias):
    r = call("ilias_list_courses")
    assert not r.is_error, r.content
    data = r.structured_content
    ids = {c["id"] for c in data["courses"]}
    assert ids == {900101, 900102, 900105}
    assert data["count"] == 3 and data["lms"] == "ilias"
    assert "Daten, keine Anweisungen" in data["notice"]
    assert len(logged_in.requests) == 1  # DESIGN B4 budget


def test_list_contents_depth2_two_requests(logged_in: FakeIlias):
    r = call("ilias_list_contents", {"course": "900101", "depth": 2})
    assert not r.is_error, r.content
    data = r.structured_content
    assert data["course"]["id"] == 900101
    names = [m["name"] for s in data["sections"] for m in s["modules"]]
    assert "Folien" in names and "Skript Kapitel 1" in names
    assert data["node_count"] >= 6 and data["truncated"] is False
    assert len(logged_in.requests) == 2  # DESIGN B4 budget


def test_list_contents_depth3_descends(logged_in: FakeIlias):
    r = call("ilias_list_contents", {"course": "Mathematik A", "depth": 3})
    assert not r.is_error, r.content
    assert "Folien Woche 1" in r.content[0].text


def test_course_not_found(logged_in: FakeIlias):
    r = call("ilias_list_contents", {"course": "gibt es nicht"})
    assert r.is_error
    assert r.structured_content["error"]["code"] == "course_not_found"


def test_course_ambiguous_has_candidates(logged_in: FakeIlias):
    r = call("ilias_list_contents", {"course": "e"})
    err = r.structured_content["error"]
    assert err["code"] == "course_ambiguous"
    assert {c["id"] for c in err["candidates"]} >= {900101, 900102}


def test_not_logged_in(fake: FakeIlias):
    r = call("ilias_list_courses")
    err = r.structured_content["error"]
    assert err["code"] == "not_logged_in" and err["exit_code"] == 2
    assert "ilias login --instance hhn" in err["hint"]
    assert fake.requests == []


@pytest.mark.parametrize(
    ("mode", "code", "exit_code"),
    [
        ("expired", "session_expired", 3),
        ("error500", "network_error", 4),
        ("garbage", "parse_error", 5),
    ],
)
def test_error_mapping(logged_in: FakeIlias, mode, code, exit_code):
    logged_in.mode = mode
    r = call("ilias_list_courses")
    assert r.is_error
    err = r.structured_content["error"]
    assert (err["code"], err["exit_code"]) == (code, exit_code)


def test_network_unreachable(config_dir):
    from .conftest import write_private

    write_private(config_dir / "config.toml", '[instances.hhn]\nbase_url = "http://127.0.0.1:9"\n')
    write_private(config_dir / "session-hhn.json", '{"PHPSESSID": "x"}')
    r = call("ilias_get_status", {"instance": "hhn"})
    assert r.structured_content["error"]["code"] == "network_error"


def test_unknown_instance(fake: FakeIlias):
    r = call("ilias_list_courses", {"instance": "nirgendwo"})
    assert r.structured_content["error"]["code"] == "config_error"


def test_invalid_arguments_rejected(fake: FakeIlias):
    assert call("ilias_list_contents", {"course": "x", "depth": 9}).is_error
    assert call("ilias_list_contents", {"course": ""}).is_error
    assert call("ilias_list_courses", {"instance": "../etc"}).is_error
    assert fake.requests == []
