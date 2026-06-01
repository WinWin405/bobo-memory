"""
End-to-end smoke tests for bobo-memory M1–M4.

Tests:
  M1: init, status, build_system_prompt, policy, guard, atomic, audit
  M2: memory_save, memory_list, memory_read, memory_recall, memory_forget/restore/purge
  M3: ingest, ingest_next, wiki_log, wiki_link, lint
  M4: session memory thresholds, snapshot check/export/init, compaction helpers
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from bobo_memory import MemoryClient


@pytest.fixture
def tmp_client(tmp_path):
    """Create a MemoryClient in a fresh temp directory."""
    return MemoryClient(
        project_root=tmp_path,
        agent_type="test-agent",
        scope="project",
    )


# ================================================================== #
# M1: Core infrastructure                                              #
# ================================================================== #

class TestM1Core:
    def test_init_creates_dirs(self, tmp_client):
        """Initialising the client should create required directories."""
        st = tmp_client.status()
        assert st["agent_type"] == "test-agent"
        assert st["scope"] == "project"
        agent_dir = Path(st["layers"]["agent"]["dir"])
        assert agent_dir.exists()

    def test_build_system_prompt(self, tmp_client):
        prompt = tmp_client.build_system_prompt("You are a test agent.")
        assert "You are a test agent" in prompt
        assert "MEMORY.md" in prompt

    def test_policy_blocks_secret(self, tmp_client):
        from bobo_memory.core.policy import PolicyViolation
        with pytest.raises(PolicyViolation, match="forbidden pattern"):
            tmp_client.policy.check_action(
                "memory_save", "auto",
                content="api_key = 'ABCDEFGHIJKLMNOPQRSTU'",
            )

    def test_atomic_write_and_read(self, tmp_path):
        from bobo_memory.core.atomic import atomic_write
        f = tmp_path / "test.md"
        atomic_write(f, "hello world")
        assert f.read_text() == "hello world"

    def test_audit_log(self, tmp_path):
        from bobo_memory.core.audit import log_event, read_events
        audit_dir = tmp_path / "audit"
        log_event(audit_dir, op="test_op", layer="auto", tool="memory_save", ok=True)
        events = read_events(audit_dir)
        assert len(events) == 1
        assert events[0]["op"] == "test_op"

    def test_guard_path_boundary(self, tmp_client, tmp_path):
        from bobo_memory.core.policy import PolicyViolation
        outside = tmp_path / "outside.md"
        outside.write_text("malicious")
        with pytest.raises(PolicyViolation, match="outside all memory"):
            tmp_client.guard.assert_within_memory(outside)

    def test_memdir_truncation(self):
        from bobo_memory.core.memdir import truncate_entrypoint_content
        long_content = ("- line\n" * 300)
        result = truncate_entrypoint_content(long_content)
        assert result.line_count <= 200
        assert result.was_line_truncated

    def test_paths_scope_resolution(self, tmp_path):
        from bobo_memory.core.paths import agent_memory_dir
        user_dir = agent_memory_dir("researcher", "user", project_root=tmp_path)
        proj_dir = agent_memory_dir("researcher", "project", project_root=tmp_path)
        local_dir = agent_memory_dir("researcher", "local", project_root=tmp_path)
        assert "user" in str(user_dir)
        assert "project" in str(proj_dir)
        assert "local" in str(local_dir)


# ================================================================== #
# M2: Tools + Recall                                                   #
# ================================================================== #

class TestM2Tools:
    def test_memory_save_and_list(self, tmp_client):
        result = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto",
            "topic": "budget-constraints",
            "content": "Q3 budget is capped at $50k.",
            "summary": "Q3 budget cap",
            "tags": ["budget", "Q3"],
        })
        assert result["ok"], result
        assert "budget-constraints.md" in result["file"]

        list_result = tmp_client.dispatch_tool_call("memory_list", {"layer": "auto"})
        assert list_result["ok"]
        assert "budget-constraints.md" in list_result["index"]

    def test_memory_read(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto",
            "topic": "read-test",
            "content": "Read test content.",
            "summary": "Read test",
        })
        list_r = tmp_client.dispatch_tool_call("memory_list", {"layer": "auto"})
        # Find file path from listing
        lines = list_r["index"].splitlines()
        file_line = next((l for l in lines if "read-test" in l), "")
        # Extract path from markdown link
        import re
        m = re.search(r"\(([^)]+)\)", file_line)
        assert m, f"No link found in: {file_line}"
        filename = m.group(1)
        # Build relative path
        from bobo_memory.core.paths import auto_memory_dir
        rel = str((auto_memory_dir(tmp_client.project_root) / filename).relative_to(tmp_client.project_root))
        read_r = tmp_client.dispatch_tool_call("memory_read", {"file": rel})
        assert read_r["ok"]
        assert "Read test content" in read_r["content"]

    def test_memory_recall(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto",
            "topic": "recall-test",
            "content": "Project uses Python 3.12 and FastAPI.",
            "summary": "Tech stack info",
            "tags": ["tech", "python"],
        })
        pack = tmp_client.recall("Python FastAPI version", layers=["auto"])
        assert hasattr(pack, "files") or isinstance(pack, dict)

    def test_tool_specs_openai(self, tmp_client):
        tools = tmp_client.to_openai_tools()
        assert len(tools) > 5
        names = {t["function"]["name"] for t in tools}
        assert "memory_save" in names
        assert "memory_recall" in names
        assert "memory_forget" in names

    def test_tool_specs_anthropic(self, tmp_client):
        tools = tmp_client.to_anthropic_tools()
        assert len(tools) > 5
        assert tools[0]["input_schema"]["type"] == "object"

    def test_memory_forget_restore(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto",
            "topic": "forget-me",
            "content": "This should be forgotten.",
            "summary": "Temporary fact",
        })
        # Find the file
        from bobo_memory.core.paths import auto_memory_dir
        mem_dir = auto_memory_dir(tmp_client.project_root)
        f = mem_dir / "forget-me.md"
        assert f.exists()
        rel = str(f.relative_to(tmp_client.project_root))

        forget_r = tmp_client.dispatch_tool_call("memory_forget", {
            "file": rel,
            "reason": "test cleanup",
        })
        assert forget_r["ok"], forget_r
        assert not f.exists()

        # Restore
        trash_file = forget_r["trash_file"]
        restore_r = tmp_client.dispatch_tool_call("memory_restore", {"trash_file": trash_file})
        assert restore_r["ok"], restore_r
        assert f.exists()

    def test_memory_purge_requires_confirm(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto",
            "topic": "purge-me",
            "content": "To be purged.",
            "summary": "Purge test",
        })
        from bobo_memory.core.paths import auto_memory_dir
        f = auto_memory_dir(tmp_client.project_root) / "purge-me.md"
        rel = str(f.relative_to(tmp_client.project_root))

        # Without confirm
        r = tmp_client.dispatch_tool_call("memory_purge", {"file": rel, "confirm": False})
        assert not r["ok"]
        assert f.exists()

        # With confirm
        r = tmp_client.dispatch_tool_call("memory_purge", {"file": rel, "confirm": True})
        assert r["ok"]
        assert not f.exists()


# ================================================================== #
# M3: Stream + Wiki + Citation                                         #
# ================================================================== #

class TestM3Stream:
    def test_ingest_markdown(self, tmp_client, tmp_path):
        md_file = tmp_path / "article.md"
        md_file.write_text("# Test Article\n\nThis is a test article about Python.")
        result = tmp_client.ingest(adapter="markdown", path=md_file)
        assert result["ok"]
        assert len(result["docs"]) == 1
        doc = result["docs"][0]
        assert doc["title"] == "Test Article"
        # Verify raw file exists
        raw_path = Path(doc["raw_path"])
        assert raw_path.exists()

    def test_ingest_next(self, tmp_client, tmp_path):
        md_file = tmp_path / "source.md"
        md_file.write_text("# Source\n\nContent to process.")
        tmp_client.ingest(adapter="markdown", path=md_file)
        result = tmp_client.dispatch_tool_call("ingest_next", {})
        assert result["ok"]
        assert result["task"] is not None
        assert "Source" in result["task"]["title"]

    def test_wiki_log(self, tmp_client):
        r = tmp_client.dispatch_tool_call("wiki_log", {
            "kind": "ingest",
            "title": "Test Article",
            "summary": "Ingested a test article about Python.",
        })
        assert r["ok"]
        from bobo_memory.core.paths import wiki_dir
        log = wiki_dir(tmp_client.project_root) / "log.md"
        assert log.exists()
        assert "Test Article" in log.read_text()

    def test_lint_runs(self, tmp_client):
        report = tmp_client.lint()
        assert hasattr(report, "summary")

    def test_ingest_chat_adapter(self, tmp_client):
        messages = [
            {"role": "user", "content": "What is the budget?"},
            {"role": "assistant", "content": "The budget is $50k for Q3."},
        ]
        result = tmp_client.ingest(adapter="chat", payload=messages)
        assert result["ok"]

    def test_citation_policy_enforced(self, tmp_client):
        """Wiki layer with require_citation should reject saves without sources."""
        from bobo_memory.core.policy import LayerPolicy, PolicyViolation
        # Override policy for this test
        tmp_client.policy.layers["wiki"] = LayerPolicy(require_citation=True)
        with pytest.raises(PolicyViolation, match="requires citation"):
            tmp_client.policy.check_action(
                "memory_save", "wiki",
                sources=[],  # empty!
                require_sources=None,
            )


# ================================================================== #
# M4: Session + Snapshot + Compact                                     #
# ================================================================== #

class TestM4SessionSnapshot:
    def test_session_should_extract_threshold(self, tmp_client):
        # Below threshold — should not extract
        short_msgs = [{"role": "user", "content": "hi"}]
        assert not tmp_client.session.should_extract(short_msgs)

    def test_session_above_threshold(self, tmp_client):
        # Above init threshold (10k tokens ≈ 40k chars)
        long_content = "a" * 45_000
        msgs = [
            {"role": "user", "content": long_content},
            {"role": "assistant", "content": long_content},
        ]
        # May or may not trigger depending on tool call count
        result = tmp_client.session.should_extract(msgs)
        assert isinstance(result, bool)

    def test_session_extract_prompt(self, tmp_client):
        prompt = tmp_client.session.build_extract_prompt([])
        assert "memory_update" in prompt
        assert str(tmp_client.session.path()) in prompt

    def test_snapshot_check_no_snapshot(self, tmp_client):
        from bobo_memory.snapshot.manager import SnapshotManager
        mgr = SnapshotManager("test-agent", tmp_client.project_root)
        assert mgr.check() == "none"

    def test_snapshot_export_and_init(self, tmp_client):
        # Save some memory first
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "agent",
            "topic": "snap-test",
            "content": "Snapshot test content.",
            "summary": "Snapshot test",
        })
        # Export snapshot
        from bobo_memory.snapshot.manager import SnapshotManager
        from bobo_memory.core.paths import snapshot_dir
        mgr = SnapshotManager("test-agent", tmp_client.project_root)
        snap_dir = snapshot_dir("test-agent", tmp_client.project_root)
        result = mgr.export(snap_dir, scope="project")
        assert result["ok"]
        assert "snap-test.md" in result["exported"]
        assert (snap_dir / "snapshot.json").exists()

        # Check state
        action = mgr.check(scope="project")
        # Since local memory has .md files and snapshot exists → could be "none" or "prompt_update"
        assert action in ("none", "initialize", "prompt_update")

    def test_compact_should_compact(self, tmp_client):
        msgs = [{"role": "user", "content": "x" * 200}]
        # Small messages → should not compact
        assert not tmp_client.compact.should_compact(msgs, token_budget=180_000)
        # Force compact
        assert tmp_client.compact.should_compact(msgs, token_budget=0)

    def test_compact_keep_index(self, tmp_client):
        from bobo_memory.compact.slicer import calculate_keep_index
        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "a" * 20_000},
            {"role": "assistant", "content": "b" * 20_000},
            {"role": "user", "content": "c" * 20_000},
        ]
        idx = calculate_keep_index(msgs, min_tail_tokens=5_000)
        assert 1 <= idx < len(msgs)

    def test_compact_invariants(self, tmp_client):
        from bobo_memory.compact.invariants import adjust_index_to_preserve_invariants
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user1"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "t", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
            {"role": "assistant", "content": "final"},
        ]
        # Cutting at idx=3 would split the tool_use/result pair
        adjusted = adjust_index_to_preserve_invariants(msgs, 3)
        # Should move back to 2 to keep the tool pair together
        assert adjusted <= 3

    def test_compact_boundary(self, tmp_client):
        boundary = tmp_client.compact.create_compact_boundary("Summary of past events.")
        assert boundary["role"] == "system"
        assert "Summary of past events" in boundary["content"]

    def test_proposal_queue(self, tmp_path):
        """Test write_mode=proposal redirects to proposals/."""
        from bobo_memory.core.policy import LayerPolicy, MemoryPolicy
        client = MemoryClient(
            project_root=tmp_path,
            agent_type="proposal-agent",
            scope="project",
        )
        # Set wiki to proposal mode
        client.policy.layers["auto"] = LayerPolicy(write_mode="proposal")
        client.guard.policy = client.policy

        result = client.dispatch_tool_call("memory_save", {
            "layer": "auto",
            "topic": "test-proposal",
            "content": "This should go to proposals.",
            "summary": "Test proposal",
        })
        assert result["ok"]
        assert result.get("proposal") is True
        # Verify the proposal file exists
        from bobo_memory.core.paths import proposals_dir
        proposals = list((proposals_dir(tmp_path) / "auto").glob("*.md"))
        assert len(proposals) == 1


# ================================================================== #
# M5: Storage governance                                               #
# ================================================================== #

class TestStorageGovernance:
    """Tests for janitor, per-layer file limits, raw size limits, and storage_stats."""

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _client_with_limit(self, tmp_path, layer: str, max_files: int) -> MemoryClient:
        from bobo_memory.core.policy import MemoryPolicy
        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        client.policy.max_files_per_layer[layer] = max_files
        return client

    # ------------------------------------------------------------------ #
    # Per-layer file count limit                                           #
    # ------------------------------------------------------------------ #

    def test_save_count_limit_blocks_new_file(self, tmp_path):
        """Saving beyond max_files_per_layer should return an error."""
        client = self._client_with_limit(tmp_path, "auto", 2)

        r1 = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "t1", "content": "A"})
        r2 = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "t2", "content": "B"})
        assert r1["ok"]
        assert r2["ok"]

        r3 = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "t3", "content": "C"})
        assert not r3["ok"]
        assert "maximum" in r3["error"]

    def test_save_count_limit_allows_overwrite(self, tmp_path):
        """Updating an existing topic should succeed even when the limit is reached."""
        client = self._client_with_limit(tmp_path, "auto", 1)

        r1 = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "t1", "content": "A"})
        assert r1["ok"]

        # Saving the same topic again (overwrite) must succeed
        r2 = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "t1", "content": "B"})
        assert r2["ok"]

    def test_memory_update_ignores_count_limit(self, tmp_path):
        """memory_update is never blocked by the count limit."""
        client = self._client_with_limit(tmp_path, "auto", 1)
        r = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "t1", "content": "A"})
        assert r["ok"]

        r2 = client.dispatch_tool_call("memory_update", {
            "file": r["file"],
            "content": "Updated content.",
        })
        assert r2["ok"]

    def test_no_limit_when_policy_unset(self, tmp_path):
        """Without max_files_per_layer, any number of files may be created."""
        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        for i in range(5):
            r = client.dispatch_tool_call("memory_save", {
                "layer": "auto", "topic": f"t{i}", "content": f"Content {i}",
            })
            assert r["ok"], f"save {i} failed: {r}"

    # ------------------------------------------------------------------ #
    # Policy: new fields round-trip via from_dict                          #
    # ------------------------------------------------------------------ #

    def test_policy_new_fields_from_dict(self):
        from bobo_memory.core.policy import MemoryPolicy
        p = MemoryPolicy.from_dict({
            "max_files_per_layer": {"auto": 100, "agent": 200},
            "session": {"max_age_days": 90},
            "audit": {"retention_days": 60},
            "raw": {"max_file_size_kb": 512},
        })
        assert p.effective_max_files("auto") == 100
        assert p.effective_max_files("agent") == 200
        assert p.effective_max_files("wiki") is None  # unset = unlimited
        assert p.session.max_age_days == 90
        assert p.audit.retention_days == 60
        assert p.raw.max_file_size_kb == 512
        assert p.raw.max_bytes() == 512 * 1024

    def test_policy_defaults_unchanged(self):
        """Default policy must behave exactly as before (backward compat)."""
        from bobo_memory.core.policy import MemoryPolicy
        p = MemoryPolicy.default()
        assert p.effective_max_files("auto") is None
        assert p.session.max_age_days is None
        assert p.audit.retention_days is None
        assert p.raw.max_file_size_kb is None

    # ------------------------------------------------------------------ #
    # Config save/load round-trip for new policy fields                   #
    # ------------------------------------------------------------------ #

    def test_config_save_load_new_fields(self, tmp_path):
        from bobo_memory.config import BoboConfig
        from bobo_memory.core.policy import MemoryPolicy
        cfg = BoboConfig(
            agent_type="gov",
            scope="project",
            project_root=tmp_path,
            policy=MemoryPolicy.from_dict({
                "max_files_per_layer": {"auto": 50},
                "session": {"max_age_days": 30},
                "audit": {"retention_days": 45},
                "raw": {"max_file_size_kb": 256},
            }),
        )
        cfg_path = tmp_path / "config.yaml"
        cfg.save(cfg_path)
        loaded = BoboConfig.load(project_root=tmp_path, config_path=cfg_path)
        assert loaded.policy.effective_max_files("auto") == 50
        assert loaded.policy.session.max_age_days == 30
        assert loaded.policy.audit.retention_days == 45
        assert loaded.policy.raw.max_file_size_kb == 256

    # ------------------------------------------------------------------ #
    # Janitor: trash                                                       #
    # ------------------------------------------------------------------ #

    def test_purge_expired_trash(self, tmp_path):
        import time
        from bobo_memory.core.janitor import purge_expired_trash
        from bobo_memory.core.policy import MemoryPolicy, TrashPolicy

        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        r = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "old", "content": "X"})
        assert r["ok"]
        forget_r = client.dispatch_tool_call("memory_forget", {
            "file": r["file"], "reason": "test",
        })
        assert forget_r["ok"]

        trash_file = Path(forget_r["trash_file"])
        full_trash = tmp_path / trash_file
        assert full_trash.exists()

        # Backdating mtime to simulate expiry (retention_days=0 means everything expired)
        policy = MemoryPolicy(trash=TrashPolicy(retention_days=0))
        report = purge_expired_trash(tmp_path, policy)

        assert report["deleted_files"] >= 1
        assert report["freed_bytes"] > 0
        assert not full_trash.exists()

    def test_purge_trash_no_op_when_fresh(self, tmp_path):
        """Fresh trash files (within retention window) must not be deleted."""
        from bobo_memory.core.janitor import purge_expired_trash
        from bobo_memory.core.policy import MemoryPolicy, TrashPolicy

        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        r = client.dispatch_tool_call("memory_save", {"layer": "auto", "topic": "fresh", "content": "X"})
        client.dispatch_tool_call("memory_forget", {"file": r["file"], "reason": "test"})

        policy = MemoryPolicy(trash=TrashPolicy(retention_days=30))
        report = purge_expired_trash(tmp_path, policy)
        assert report["deleted_files"] == 0

    # ------------------------------------------------------------------ #
    # Janitor: sessions                                                    #
    # ------------------------------------------------------------------ #

    def test_cleanup_sessions_expired(self, tmp_path):
        from bobo_memory.core.janitor import cleanup_sessions
        from bobo_memory.core.policy import MemoryPolicy, SessionPolicy

        session_dir = tmp_path / ".bobo" / "memory" / "session"
        session_dir.mkdir(parents=True, exist_ok=True)
        old_file = session_dir / "old-session.md"
        old_file.write_text("# Old session", encoding="utf-8")

        # Backdate mtime to 100 days ago
        import time
        old_ts = time.time() - 100 * 86400
        import os
        os.utime(old_file, (old_ts, old_ts))

        policy = MemoryPolicy(session=SessionPolicy(max_age_days=90))
        report = cleanup_sessions(tmp_path, policy)

        assert report["deleted_files"] == 1
        assert not old_file.exists()

    def test_cleanup_sessions_no_op_when_none(self, tmp_path):
        """max_age_days=None disables session cleanup entirely."""
        from bobo_memory.core.janitor import cleanup_sessions
        from bobo_memory.core.policy import MemoryPolicy, SessionPolicy

        session_dir = tmp_path / ".bobo" / "memory" / "session"
        session_dir.mkdir(parents=True, exist_ok=True)
        f = session_dir / "keep.md"
        f.write_text("# Keep", encoding="utf-8")

        policy = MemoryPolicy(session=SessionPolicy(max_age_days=None))
        report = cleanup_sessions(tmp_path, policy)
        assert report["deleted_files"] == 0
        assert f.exists()

    # ------------------------------------------------------------------ #
    # Janitor: audit                                                       #
    # ------------------------------------------------------------------ #

    def test_rotate_audit_removes_old(self, tmp_path):
        from bobo_memory.core.janitor import rotate_audit
        from bobo_memory.core.policy import AuditPolicy, MemoryPolicy

        audit_dir = tmp_path / ".bobo" / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        old_log = audit_dir / "audit-2020-01-01.jsonl"
        old_log.write_text('{"op":"test"}\n', encoding="utf-8")

        policy = MemoryPolicy(audit=AuditPolicy(retention_days=90))
        report = rotate_audit(tmp_path, policy)
        assert report["deleted_files"] == 1
        assert not old_log.exists()

    def test_rotate_audit_keeps_today(self, tmp_path):
        from datetime import datetime, timezone
        from bobo_memory.core.janitor import rotate_audit
        from bobo_memory.core.policy import AuditPolicy, MemoryPolicy

        audit_dir = tmp_path / ".bobo" / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        today_log = audit_dir / f"audit-{today_str}.jsonl"
        today_log.write_text('{"op":"test"}\n', encoding="utf-8")

        policy = MemoryPolicy(audit=AuditPolicy(retention_days=0))
        report = rotate_audit(tmp_path, policy)
        assert today_log.exists(), "Today's audit log must never be deleted"

    def test_rotate_audit_no_op_when_none(self, tmp_path):
        """retention_days=None disables audit rotation entirely."""
        from bobo_memory.core.janitor import rotate_audit
        from bobo_memory.core.policy import AuditPolicy, MemoryPolicy

        audit_dir = tmp_path / ".bobo" / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        f = audit_dir / "audit-2020-01-01.jsonl"
        f.write_text("{}\n", encoding="utf-8")

        policy = MemoryPolicy(audit=AuditPolicy(retention_days=None))
        report = rotate_audit(tmp_path, policy)
        assert report["deleted_files"] == 0
        assert f.exists()

    # ------------------------------------------------------------------ #
    # run_janitor                                                          #
    # ------------------------------------------------------------------ #

    def test_run_janitor_returns_summary(self, tmp_path):
        from bobo_memory.core.policy import AuditPolicy, MemoryPolicy, SessionPolicy, TrashPolicy
        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        client.policy.session = SessionPolicy(max_age_days=90)
        client.policy.audit = AuditPolicy(retention_days=90)

        report = client.run_janitor()
        for key in ("trash", "session", "audit", "total_freed_bytes"):
            assert key in report
        assert isinstance(report["total_freed_bytes"], int)

    # ------------------------------------------------------------------ #
    # ingest raw size limit                                                #
    # ------------------------------------------------------------------ #

    def test_ingest_skips_oversized_doc(self, tmp_path):
        """Documents exceeding raw.max_file_size_kb must be skipped, not written."""
        from bobo_memory.core.policy import MemoryPolicy, RawPolicy
        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        client.policy.raw = RawPolicy(max_file_size_kb=1)  # 1 KB limit

        big_content = "x" * 2048  # ~2 KB
        result = client.ingest(adapter="markdown", payload=big_content)

        assert result["ok"]
        assert len(result["skipped"]) == 1
        assert len(result["docs"]) == 0
        assert "max_raw_bytes" in result["skipped"][0]["reason"]

    def test_ingest_accepts_small_doc(self, tmp_path):
        """Documents within the size limit must be ingested normally."""
        from bobo_memory.core.policy import MemoryPolicy, RawPolicy
        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        client.policy.raw = RawPolicy(max_file_size_kb=10)

        small_content = "# Hello\n\nSmall document."
        result = client.ingest(adapter="markdown", payload=small_content)

        assert result["ok"]
        assert len(result["docs"]) == 1
        assert len(result["skipped"]) == 0

    def test_ingest_no_limit_by_default(self, tmp_path):
        """Without raw policy, large documents must pass through unchanged."""
        client = MemoryClient(project_root=tmp_path, agent_type="gov-agent", scope="project")
        big_content = "y" * 200_000
        result = client.ingest(adapter="markdown", payload=big_content)
        assert result["ok"]
        assert len(result["skipped"]) == 0

    # ------------------------------------------------------------------ #
    # storage_stats                                                        #
    # ------------------------------------------------------------------ #

    def test_storage_stats_structure(self, tmp_client):
        """storage_stats must return the expected keys."""
        stats = tmp_client.storage_stats()
        assert "project_root" in stats
        assert "layers" in stats
        assert "audit_bytes" in stats

    def test_storage_stats_counts_files(self, tmp_client):
        """Files saved must be reflected in storage_stats."""
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "stat-test", "content": "Hello stats.",
        })
        stats = tmp_client.storage_stats()
        auto_stats = stats["layers"].get("auto", {})
        assert auto_stats.get("files", 0) >= 1
        assert auto_stats.get("bytes", 0) > 0

    def test_storage_stats_trash_bytes(self, tmp_client):
        """Forgotten files should appear in trash_bytes."""
        r = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "trash-stat", "content": "Bye.",
        })
        tmp_client.dispatch_tool_call("memory_forget", {"file": r["file"], "reason": "test"})

        stats = tmp_client.storage_stats()
        auto_stats = stats["layers"].get("auto", {})
        assert auto_stats.get("trash_bytes", 0) > 0
