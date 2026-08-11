"""Structural validation of the starter snippet library.

Not testing specific snippet content (that changes as the library grows) —
testing the invariants that would break the app if violated: unique ids,
every {{placeholder}} in a command has a matching params entry and
vice versa, no empty required fields.
"""
import re

from seed_data import build_seed_data


def test_build_seed_data_has_multiple_categories():
    data = build_seed_data()
    assert len(data["categories"]) >= 5


def test_every_category_has_required_fields():
    data = build_seed_data()
    for cat in data["categories"]:
        assert cat["id"]
        assert cat["label"]
        assert cat["icon"]
        assert isinstance(cat["snippets"], list)
        assert len(cat["snippets"]) > 0


def test_snippet_ids_are_unique_within_each_category():
    data = build_seed_data()
    for cat in data["categories"]:
        ids = [s["id"] for s in cat["snippets"]]
        assert len(ids) == len(set(ids)), f"duplicate snippet id in category {cat['id']!r}"


def test_snippet_ids_are_globally_unique():
    data = build_seed_data()
    ids = [s["id"] for c in data["categories"] for s in c["snippets"]]
    assert len(ids) == len(set(ids))


def test_no_duplicate_commands_across_the_whole_library():
    data = build_seed_data()
    commands = [s["command"] for c in data["categories"] for s in c["snippets"]]
    assert len(commands) == len(set(commands))


def test_every_snippet_has_title_command_and_description():
    data = build_seed_data()
    for cat in data["categories"]:
        for s in cat["snippets"]:
            assert s["title"].strip(), f"blank title in {cat['id']}/{s['id']}"
            assert s["command"].strip(), f"blank command in {cat['id']}/{s['id']}"
            assert s["description"].strip(), f"blank description in {cat['id']}/{s['id']}"


def test_params_exactly_match_placeholders_in_command():
    data = build_seed_data()
    for cat in data["categories"]:
        for s in cat["snippets"]:
            placeholders = set(re.findall(r"\{\{(\w+)\}\}", s["command"]))
            param_names = {p["name"] for p in s["params"]}
            assert placeholders == param_names, (
                f"{cat['id']}/{s['id']}: command placeholders {placeholders} "
                f"don't match params {param_names}"
            )
