"""The permission model: allowlists, destructive confirmation, action budgets."""

from __future__ import annotations

from iris.computer import Action, ActionKind
from iris.computer.permissions import Grants, PermissionModel, host_of, matches_host, matches_title


def test_host_of_handles_bare_and_full_urls():
    assert host_of("https://example.com/a/b?c=1") == "example.com"
    assert host_of("example.com/path") == "example.com"
    assert host_of("") == ""
    assert host_of("/just/a/path") == ""


def test_suffix_match_is_on_label_boundaries():
    assert matches_host("example.com", ["example.com"])[0]
    assert matches_host("docs.example.com", ["example.com"])[0]
    # The case the audit names explicitly:
    assert not matches_host("evil-example.com", ["example.com"])[0]
    assert not matches_host("example.com.attacker.net", ["example.com"])[0]


def test_the_wildcard_spelling_is_the_same_label_rule():
    assert matches_host("docs.dev", ["*.docs.dev"])[0]
    assert not matches_host("dev", ["*.docs.dev"])[0]
    assert not matches_host("evil-docs.dev", ["*.docs.dev"])[0]


def test_title_matching_is_a_plain_suffix():
    assert matches_title("Sign in — Example", ["Example"])[0]
    assert not matches_title("Example phishing", ["Example"])[0]


def test_empty_allowlists_allow_nothing():
    model = PermissionModel()
    assert model.check(Action(ActionKind.NAVIGATE, target="https://example.com")).refused
    assert model.check(Action(ActionKind.CLICK, target="#go", window="Example")).refused


def test_navigate_requires_an_allowlisted_host():
    model = PermissionModel(allowed_hosts=["example.com"])
    assert model.check(Action(ActionKind.NAVIGATE, target="https://docs.example.com/x")).allowed
    assert model.check(Action(ActionKind.NAVIGATE, target="https://evil-example.com")).refused


def test_click_and_type_require_a_window_and_an_allowlisted_title():
    model = PermissionModel(allowed_apps=["Example"])
    assert model.check(Action(ActionKind.CLICK, target="#go", window="Sign in — Example")).allowed
    assert model.check(Action(ActionKind.CLICK, target="#go", window="")).refused
    assert model.check(Action(ActionKind.CLICK, target="#go", window="Bank of Elsewhere")).refused


def test_a_screenshot_needs_no_target():
    assert PermissionModel().check(Action(ActionKind.SCREENSHOT)).allowed


def test_the_destructive_subset_always_confirms():
    model = PermissionModel(allowed_apps=["Example"])
    assert model.needs_confirmation(Action(ActionKind.CLICK, target="#go", window="Example"))
    assert model.needs_confirmation(Action(ActionKind.TYPE, target="#q", window="Example", text="hi"))
    assert not model.needs_confirmation(Action(ActionKind.SCREENSHOT))
    assert not model.needs_confirmation(Action(ActionKind.NAVIGATE, target="https://example.com"))


def test_a_credential_field_confirms_even_when_confirmation_is_switched_off():
    model = PermissionModel(confirm_destructive=False)
    assert model.needs_confirmation(Action(ActionKind.TYPE, target="#password", window="Example", text="x"))


def test_from_csv_splits_and_drops_blank_entries():
    model = PermissionModel.from_csv(allowed_hosts=" example.com ,, docs.dev ", allowed_apps="", max_actions=3)
    assert model.allowed_hosts == ("example.com", "docs.dev")
    assert model.allowed_apps == ()
    assert model.grants.max_actions == 3


def test_a_grant_expires_by_count():
    grants = Grants(max_actions=2)
    assert grants.remaining("s") == 0
    grants.grant("s")
    assert grants.consume("s")
    assert grants.consume("s")
    assert not grants.consume("s")
    assert grants.remaining("s") == 0


def test_grants_are_per_session():
    grants = Grants(max_actions=1)
    grants.grant("a")
    assert not grants.consume("b")
    assert grants.consume("a")
