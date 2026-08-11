"""Backend API tests.

Covers the behaviors that actually matter for this app: seeding on first
run, category auto-detection/auto-creation, duplicate prevention, and —
most importantly — that concurrent writes never corrupt the data file. That
last one isn't a hypothetical: an earlier version of this app's bulk-save
path fired one write per snippet in parallel and it silently corrupted a
real user's data/snippets.yaml mid-file. test_concurrent_* below is the
regression test for that incident.
"""
import concurrent.futures
import threading

import pytest
import yaml


def _total_snippets(payload: dict) -> int:
    return sum(len(c["snippets"]) for c in payload["categories"])


# ── First run / seeding ──────────────────────────────────────────────────

def test_fresh_file_gets_seeded(client, data_file):
    assert not data_file.exists()
    res = client.get("/api/snippets")
    assert res.status_code == 200
    body = res.json()
    assert len(body["categories"]) > 0
    assert _total_snippets(body) > 100  # the starter library is ~290 snippets
    assert data_file.exists()


def test_a_category_with_zero_snippets_is_not_mistaken_for_empty(client, data_file):
    # total==0 alone must not re-trigger the seed — only a genuinely empty
    # categories list should. Otherwise a user who deletes every snippet in
    # a category would get their whole library silently reset.
    data_file.write_text(yaml.dump({
        "categories": [{"id": "custom", "label": "Custom", "icon": "▸", "snippets": []}]
    }))
    res = client.get("/api/snippets")
    body = res.json()
    assert body["categories"] == [{"id": "custom", "label": "Custom", "icon": "▸", "snippets": []}]


# ── Creating snippets ─────────────────────────────────────────────────────

def test_add_snippet_requires_only_command(client):
    res = client.post("/api/snippets", json={"command": "echo hello"})
    assert res.status_code == 200
    body = res.json()
    assert body["command"] == "echo hello"
    assert body["title"]  # derived, non-empty
    assert body["params"] == []


def test_add_snippet_extracts_params_from_command(client):
    res = client.post("/api/snippets", json={
        "command": "kubectl logs {{pod}} -n {{namespace}}", "title": "t", "category": "Kubernetes",
    })
    body = res.json()
    assert [p["name"] for p in body["params"]] == ["pod", "namespace"]


def test_add_snippet_blank_command_rejected(client):
    res = client.post("/api/snippets", json={"command": "   "})
    assert res.status_code == 400


def test_add_snippet_infers_category_from_base_command(client):
    # Not "git status" — that's already in the seed library, and this test
    # is about category inference, not duplicate handling (covered separately).
    res = client.post("/api/snippets", json={"command": "git status --short"})
    assert res.status_code == 200
    snippets = client.get("/api/snippets").json()
    git_cat = next(c for c in snippets["categories"] if c["id"] == "git")
    assert any(s["command"] == "git status --short" for s in git_cat["snippets"])


def test_add_snippet_category_keyword_fallback_never_general(client):
    # "source venv/bin/activate" has no recognizable first-token tool name —
    # must fall through to the keyword scan (finds "venv" -> Python), and
    # must never land in a literal "general" bucket.
    res = client.post("/api/snippets", json={"command": "source venv/bin/activate"})
    assert res.status_code == 200
    snippets = client.get("/api/snippets").json()
    python_cat = next(c for c in snippets["categories"] if c["id"] == "python")
    assert any(s["command"] == "source venv/bin/activate" for s in python_cat["snippets"])


def test_add_snippet_unrecognizable_command_falls_back_to_bash_not_general(client):
    res = client.post("/api/snippets", json={"command": "some-made-up-tool --flag"})
    snippets = client.get("/api/snippets").json()
    cat_ids = {c["id"] for c in snippets["categories"]}
    assert "general" not in cat_ids
    bash_cat = next(c for c in snippets["categories"] if c["id"] == "bash")
    assert any(s["command"] == "some-made-up-tool --flag" for s in bash_cat["snippets"])


def test_add_snippet_custom_category_is_created(client):
    res = client.post("/api/snippets", json={
        "command": "terraform apply", "title": "Apply", "category": "Terraform",
    })
    assert res.status_code == 200
    snippets = client.get("/api/snippets").json()
    assert any(c["label"] == "Terraform" for c in snippets["categories"])


def test_add_snippet_same_category_typed_twice_reuses_it(client):
    client.post("/api/snippets", json={"command": "terraform apply", "title": "a", "category": "Terraform"})
    client.post("/api/snippets", json={"command": "terraform plan", "title": "b", "category": "Terraform"})
    snippets = client.get("/api/snippets").json()
    terraform_cats = [c for c in snippets["categories"] if c["label"] == "Terraform"]
    assert len(terraform_cats) == 1
    assert len(terraform_cats[0]["snippets"]) == 2


# ── Duplicate prevention ──────────────────────────────────────────────────

def test_add_duplicate_command_is_rejected(client):
    first = client.post("/api/snippets", json={"command": "echo dup", "title": "a", "category": "Bash"})
    assert first.status_code == 200
    second = client.post("/api/snippets", json={"command": "echo dup", "title": "b", "category": "Bash"})
    assert second.status_code == 409


def test_duplicate_check_ignores_surrounding_whitespace(client):
    client.post("/api/snippets", json={"command": "echo dup", "title": "a", "category": "Bash"})
    res = client.post("/api/snippets", json={"command": "  echo dup  ", "title": "b", "category": "Bash"})
    assert res.status_code == 409


def test_duplicate_check_is_global_across_categories(client):
    # Same command text filed under a different category is still a
    # duplicate — the check is command-based, not scoped per category.
    client.post("/api/snippets", json={"command": "echo dup", "title": "a", "category": "Bash"})
    res = client.post("/api/snippets", json={"command": "echo dup", "title": "b", "category": "SomethingElse"})
    assert res.status_code == 409


# ── Bulk endpoint ──────────────────────────────────────────────────────────

def test_bulk_creates_all_new_snippets_in_one_call(client):
    payload = [
        {"command": f"echo bulk-{i}", "title": f"Bulk {i}", "category": "Bash"} for i in range(10)
    ]
    res = client.post("/api/snippets/bulk", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]) == 10
    assert body["skipped"] == []


def test_bulk_skips_duplicates_against_existing_data_without_failing_the_batch(client):
    client.post("/api/snippets", json={"command": "echo existing", "title": "x", "category": "Bash"})
    payload = [
        {"command": "echo existing", "title": "dup", "category": "Bash"},
        {"command": "echo new-one", "title": "new", "category": "Bash"},
    ]
    res = client.post("/api/snippets/bulk", json=payload)
    body = res.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["command"] == "echo new-one"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["command"] == "echo existing"


def test_bulk_dedupes_within_the_same_batch(client):
    # e.g. an AI-generated import that happens to list the same command twice.
    payload = [
        {"command": "echo same", "title": "first", "category": "Bash"},
        {"command": "echo same", "title": "second", "category": "Bash"},
    ]
    res = client.post("/api/snippets/bulk", json=payload)
    body = res.json()
    assert len(body["created"]) == 1
    assert len(body["skipped"]) == 1


def test_bulk_result_has_no_duplicate_ids(client):
    payload = [{"command": f"echo bulk-{i}", "title": f"Bulk {i}", "category": "Bash"} for i in range(25)]
    client.post("/api/snippets/bulk", json=payload)
    snippets = client.get("/api/snippets").json()
    ids = [s["id"] for c in snippets["categories"] for s in c["snippets"]]
    assert len(ids) == len(set(ids))


# ── Deleting ──────────────────────────────────────────────────────────────

def test_delete_snippet(client):
    created = client.post("/api/snippets", json={"command": "echo to-delete", "title": "t", "category": "Bash"}).json()
    res = client.delete(f"/api/snippets/{created['id']}")
    assert res.status_code == 200
    snippets = client.get("/api/snippets").json()
    ids = [s["id"] for c in snippets["categories"] for s in c["snippets"]]
    assert created["id"] not in ids


def test_delete_unknown_snippet_404s(client):
    res = client.delete("/api/snippets/does-not-exist")
    assert res.status_code == 404


# ── Categories ───────────────────────────────────────────────────────────

def test_add_category(client):
    res = client.post("/api/categories", json={"id": "rust", "label": "Rust", "icon": "▸"})
    assert res.status_code == 200
    snippets = client.get("/api/snippets").json()
    assert any(c["id"] == "rust" for c in snippets["categories"])


def test_add_category_duplicate_id_rejected(client):
    client.post("/api/categories", json={"id": "rust", "label": "Rust", "icon": "▸"})
    res = client.post("/api/categories", json={"id": "rust", "label": "Rust Again", "icon": "▸"})
    assert res.status_code == 400


# ── Suggest endpoint ─────────────────────────────────────────────────────

def test_suggest_endpoint(client):
    res = client.get("/api/suggest", params={"command": "docker ps -a"})
    body = res.json()
    assert body["category_id"] == "docker"
    assert body["title"] == "Docker ps -a"


# ── Concurrency / data-integrity regression tests ──────────────────────────
# This is the actual bug that corrupted a real user's data file: parallel
# writers each doing an unsynchronized read-modify-write on the same YAML
# file. These tests hit the same code path with real concurrent threads.

def test_concurrent_single_adds_produce_no_corruption_and_no_lost_writes(client, data_file):
    client.get("/api/snippets")  # ensure the file is seeded before the burst
    n = 20

    def add(i):
        return client.post("/api/snippets", json={
            "command": f"echo race-{i}", "title": f"Race {i}", "category": "RaceTest",
        })

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(add, range(n)))

    assert all(r.status_code == 200 for r in results)

    # The file must still be valid YAML, and every one of the 20 concurrent
    # writes must have landed — none silently lost, none corrupted.
    with open(data_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data is not None
    race_cat = next(c for c in data["categories"] if c["id"] == "racetest")
    assert len(race_cat["snippets"]) == n
    ids = [s["id"] for c in data["categories"] for s in c["snippets"]]
    assert len(ids) == len(set(ids))


def test_concurrent_bulk_and_single_adds_together_produce_no_corruption(client, data_file):
    client.get("/api/snippets")

    def do_bulk():
        payload = [{"command": f"echo mix-bulk-{i}", "title": f"B{i}", "category": "Mixed"} for i in range(15)]
        return client.post("/api/snippets/bulk", json=payload)

    def do_single(i):
        return client.post("/api/snippets", json={
            "command": f"echo mix-single-{i}", "title": f"S{i}", "category": "Mixed",
        })

    with concurrent.futures.ThreadPoolExecutor(max_workers=11) as pool:
        futures = [pool.submit(do_bulk)] + [pool.submit(do_single, i) for i in range(10)]
        results = [f.result() for f in futures]

    assert all(r.status_code == 200 for r in results)

    with open(data_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data is not None
    mixed_cat = next(c for c in data["categories"] if c["id"] == "mixed")
    assert len(mixed_cat["snippets"]) == 25  # 15 from the bulk call + 10 singles
    ids = [s["id"] for c in data["categories"] for s in c["snippets"]]
    assert len(ids) == len(set(ids))
