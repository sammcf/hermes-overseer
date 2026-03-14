"""Tests for overseer.response.state_restore."""

from __future__ import annotations

from overseer.response.state_restore import audit_restore_plan, classify_file_for_restore

# ---------------------------------------------------------------------------
# classify_file_for_restore
# ---------------------------------------------------------------------------


class TestClassifyFileForRestore:
    def test_state_db_is_safe(self):
        assert classify_file_for_restore("state.db") == "safe"

    def test_state_db_with_path_prefix(self):
        assert classify_file_for_restore("/home/hermes/.hermes/state.db") == "safe"

    def test_soul_md_is_audit(self):
        assert classify_file_for_restore("SOUL.md") == "audit"

    def test_soul_md_with_path(self):
        assert classify_file_for_restore("/home/hermes/.hermes/SOUL.md") == "audit"

    def test_memories_dir_file_is_audit(self):
        assert classify_file_for_restore("memories/MEMORY.md") == "audit"

    def test_memories_nested_file_is_audit(self):
        assert classify_file_for_restore("memories/USER.md") == "audit"

    def test_memories_absolute_path_is_audit(self):
        assert classify_file_for_restore("/home/hermes/.hermes/memories/MEMORY.md") == "audit"

    def test_config_yaml_is_canonical(self):
        assert classify_file_for_restore("config.yaml") == "canonical"

    def test_config_yaml_with_path(self):
        assert classify_file_for_restore("/home/hermes/.hermes/config.yaml") == "canonical"

    def test_env_is_skip(self):
        assert classify_file_for_restore(".env") == "skip"

    def test_env_with_path(self):
        assert classify_file_for_restore("/home/hermes/.hermes/.env") == "skip"

    def test_unknown_file(self):
        assert classify_file_for_restore("random_script.sh") == "unknown"

    def test_unknown_absolute_path(self):
        assert classify_file_for_restore("/home/hermes/.hermes/tools/some_tool.py") == "unknown"

    def test_unknown_log_file(self):
        assert classify_file_for_restore("logs/debug.log") == "unknown"


# ---------------------------------------------------------------------------
# audit_restore_plan
# ---------------------------------------------------------------------------


class TestAuditRestorePlan:
    def test_empty_list_returns_all_empty_categories(self):
        plan = audit_restore_plan([])
        assert plan == {
            "safe": [],
            "audit": [],
            "canonical": [],
            "skip": [],
            "unknown": [],
        }

    def test_always_returns_all_five_keys(self):
        plan = audit_restore_plan(["state.db"])
        assert set(plan.keys()) == {"safe", "audit", "canonical", "skip", "unknown"}

    def test_groups_by_category(self):
        files = [
            "state.db",
            "SOUL.md",
            "memories/MEMORY.md",
            "config.yaml",
            ".env",
            "random.sh",
        ]
        plan = audit_restore_plan(files)
        assert plan["safe"] == ["state.db"]
        assert sorted(plan["audit"]) == sorted(["SOUL.md", "memories/MEMORY.md"])
        assert plan["canonical"] == ["config.yaml"]
        assert plan["skip"] == [".env"]
        assert plan["unknown"] == ["random.sh"]

    def test_multiple_files_per_category(self):
        files = [
            "memories/MEMORY.md",
            "memories/USER.md",
            "memories/extra.md",
        ]
        plan = audit_restore_plan(files)
        assert len(plan["audit"]) == 3
        assert all(f in plan["audit"] for f in files)

    def test_preserves_order_within_category(self):
        files = ["random_a.sh", "random_b.sh", "random_c.sh"]
        plan = audit_restore_plan(files)
        assert plan["unknown"] == files

    def test_single_safe_file(self):
        plan = audit_restore_plan(["state.db"])
        assert plan["safe"] == ["state.db"]
        assert plan["audit"] == []
        assert plan["canonical"] == []
        assert plan["skip"] == []
        assert plan["unknown"] == []

    def test_mixed_absolute_and_relative_paths(self):
        files = [
            "/home/hermes/.hermes/state.db",
            "/home/hermes/.hermes/.env",
            "/home/hermes/.hermes/config.yaml",
            "/home/hermes/.hermes/memories/MEMORY.md",
        ]
        plan = audit_restore_plan(files)
        assert len(plan["safe"]) == 1
        assert len(plan["skip"]) == 1
        assert len(plan["canonical"]) == 1
        assert len(plan["audit"]) == 1
        assert len(plan["unknown"]) == 0
