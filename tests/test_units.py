"""Unit tests for the small pure helpers in app.py — slugify, derive_title,
suggest_category_id, _find_duplicate. No server, no I/O.
"""
from app import derive_title, slugify, suggest_category_id, _find_duplicate


class TestSlugify:
    def test_lowercases_and_hyphenates(self):
        assert slugify("AWS CLI") == "aws-cli"

    def test_strips_leading_trailing_punctuation(self):
        assert slugify("  --Terraform!!  ") == "terraform"

    def test_empty_string_falls_back_to_general(self):
        assert slugify("") == "general"

    def test_only_punctuation_falls_back_to_general(self):
        assert slugify("!!!") == "general"


class TestDeriveTitle:
    def test_strips_placeholders(self):
        title = derive_title("kubectl logs {{pod}} -n {{namespace}}")
        assert "{{" not in title

    def test_capitalizes_first_letter(self):
        assert derive_title("git status").startswith("Git")

    def test_truncates_long_commands(self):
        long_cmd = "echo " + "x" * 100
        assert len(derive_title(long_cmd)) <= 60

    def test_empty_command_gets_a_fallback_title(self):
        assert derive_title("") == "Untitled snippet"

    def test_command_that_is_only_placeholders_falls_back_to_raw_text(self):
        # Stripping {{}} would leave nothing — must fall back to the raw
        # command rather than producing an empty title.
        title = derive_title("{{cmd}}")
        assert title != ""


class TestSuggestCategoryId:
    def test_known_base_command(self):
        assert suggest_category_id("kubectl get pods") == "kubernetes"
        assert suggest_category_id("docker ps") == "docker"
        assert suggest_category_id("git status") == "git"

    def test_shell_builtin_falls_back_to_keyword_scan(self):
        # "source" itself isn't a recognized tool — must find "venv" in the
        # rest of the command via the keyword fallback, not just give up.
        assert suggest_category_id("source venv/bin/activate") == "python"

    def test_unrecognizable_command_falls_back_to_bash_never_general(self):
        assert suggest_category_id("some-made-up-tool --flag") == "bash"

    def test_never_returns_general(self):
        samples = [
            "kubectl get pods", "docker ps", "git log", "pip install x",
            "npm install", "curl https://x", "aws s3 ls", "az login",
            "grep -r x .", "cd /tmp", "totally-unknown-binary --help", "",
        ]
        for cmd in samples:
            assert suggest_category_id(cmd) != "general"


class TestFindDuplicate:
    def test_finds_exact_match(self):
        data = {"categories": [{"id": "git", "snippets": [{"id": "git-1", "command": "git status"}]}]}
        assert _find_duplicate(data, "git status") is not None

    def test_no_match_returns_none(self):
        data = {"categories": [{"id": "git", "snippets": [{"id": "git-1", "command": "git status"}]}]}
        assert _find_duplicate(data, "git diff") is None

    def test_ignores_surrounding_whitespace(self):
        data = {"categories": [{"id": "git", "snippets": [{"id": "git-1", "command": "git status"}]}]}
        assert _find_duplicate(data, "  git status  ") is not None

    def test_empty_data_has_no_duplicates(self):
        assert _find_duplicate({"categories": []}, "git status") is None
