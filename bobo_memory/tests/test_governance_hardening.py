"""
Governance-hardening tests.

Covers the fixes that close gaps in the policy → guard → atomic → audit
pipeline:
  - agent_type / topic sanitisation (path traversal, reserved names)
  - wiki_link / wiki_log going through policy + guard
  - actor propagation from dispatch_tool_call into policy checks
  - memory_update honouring write_mode=proposal
  - frontmatter robustness (created-date preservation, YAML-unsafe values)
  - proposal header YAML safety + policy check on accept
  - recall zero-score filtering
  - atomic_write durability + UTF-8 round-trip
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bobo_memory import MemoryClient


@pytest.fixture
def tmp_client(tmp_path):
    return MemoryClient(project_root=tmp_path, agent_type="hard-agent", scope="project")


# ================================================================== #
# Path / name sanitisation                                             #
# ================================================================== #

class TestSanitisation:
    def test_agent_type_dots_rejected(self, tmp_path):
        from bobo_memory.core.paths import agent_memory_dir
        for bad in ("..", ".", "", "   ", "..."):
            with pytest.raises(ValueError):
                agent_memory_dir(bad, "project", project_root=tmp_path)

    def test_agent_type_namespace_still_works(self, tmp_path):
        from bobo_memory.core.paths import agent_memory_dir
        d = agent_memory_dir("my-plugin:worker", "project", project_root=tmp_path)
        assert "my-plugin-worker" in str(d)

    def test_topic_reserved_name_rejected(self, tmp_client):
        for bad_topic in ("MEMORY", "MEMORY.md", "INDEX"):
            r = tmp_client.dispatch_tool_call("memory_save", {
                "layer": "auto", "topic": bad_topic, "content": "x",
            })
            if bad_topic.startswith("MEMORY"):
                assert not r["ok"], f"topic {bad_topic!r} must be rejected"
                assert "reserved" in r["error"]

    def test_topic_dots_only_rejected(self, tmp_client):
        for bad_topic in ("..", ".", "..."):
            r = tmp_client.dispatch_tool_call("memory_save", {
                "layer": "auto", "topic": bad_topic, "content": "x",
            })
            assert not r["ok"], f"topic {bad_topic!r} must be rejected"

    def test_topic_traversal_stays_inside(self, tmp_client, tmp_path):
        r = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "../../outside", "content": "x",
        })
        assert r["ok"], r
        written = tmp_path / r["file"]
        auto_dir = tmp_path / ".bobo" / "memory" / "auto"
        assert auto_dir in written.parents

    def test_update_protocol_file_rejected(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "seed", "content": "x",
        })
        r = tmp_client.dispatch_tool_call("memory_update", {
            "file": ".bobo/memory/auto/MEMORY.md", "content": "hijacked",
        })
        assert not r["ok"]
        assert "protocol file" in r["error"]


# ================================================================== #
# Wiki tools go through policy + guard                                  #
# ================================================================== #

class TestWikiGovernance:
    def test_wiki_log_secret_blocked(self, tmp_client):
        r = tmp_client.dispatch_tool_call("wiki_log", {
            "kind": "event", "title": "leak",
            "summary": "api_key = 'ABCDEFGHIJKLMNOPQRSTUVWX'",
        })
        assert not r["ok"]
        assert "forbidden pattern" in r["error"]

    def test_wiki_log_writable_by_enforced(self, tmp_client):
        from bobo_memory.core.policy import LayerPolicy
        tmp_client.policy.layers["wiki"] = LayerPolicy(writable_by=["system"])
        r = tmp_client.dispatch_tool_call("wiki_log", {
            "kind": "event", "title": "t", "summary": "s",
        })
        assert not r["ok"]
        assert "writable_by" in r["error"]

    def test_wiki_link_writable_by_enforced(self, tmp_client):
        from bobo_memory.core.policy import LayerPolicy
        tmp_client.policy.layers["wiki"] = LayerPolicy(writable_by=["system"])
        r = tmp_client.dispatch_tool_call("wiki_link", {
            "from_topic": "a", "to_topic": "b",
        })
        assert not r["ok"]

    def test_wiki_log_rotates_at_size_limit(self, tmp_client, tmp_path):
        from bobo_memory.core.policy import LayerPolicy
        # 1 KB layer limit → second entry forces rotation
        tmp_client.policy.layers["wiki"] = LayerPolicy(max_file_size_kb=1)
        big = "x" * 600
        r1 = tmp_client.dispatch_tool_call("wiki_log", {"kind": "e", "title": "1", "summary": big})
        assert r1["ok"], r1
        r2 = tmp_client.dispatch_tool_call("wiki_log", {"kind": "e", "title": "2", "summary": big})
        assert r2["ok"], r2
        wiki = tmp_path / ".bobo" / "memory" / "wiki"
        rotated = list(wiki.glob("log-*.md"))
        assert len(rotated) == 1, "old log must be rotated out"
        assert "| 2" in (wiki / "log.md").read_text(encoding="utf-8")


# ================================================================== #
# Actor propagation                                                     #
# ================================================================== #

class TestActorPropagation:
    def test_actor_reaches_policy(self, tmp_client):
        """An actor outside writable_by must be denied even via dispatch."""
        from bobo_memory.core.policy import LayerPolicy
        tmp_client.policy.layers["auto"] = LayerPolicy(writable_by=["system"])
        denied = tmp_client.dispatch_tool_call(
            "memory_save",
            {"layer": "auto", "topic": "t", "content": "c"},
            actor="agent",
        )
        assert not denied["ok"]
        allowed = tmp_client.dispatch_tool_call(
            "memory_save",
            {"layer": "auto", "topic": "t", "content": "c"},
            actor="system",
        )
        assert allowed["ok"], allowed

    def test_actor_recorded_in_audit(self, tmp_client):
        tmp_client.dispatch_tool_call(
            "memory_save",
            {"layer": "auto", "topic": "audited", "content": "c"},
            actor="system",
        )
        events = tmp_client.audit_log(limit=5)
        save_events = [e for e in events if e["op"] == "memory_save" and e["ok"]]
        assert save_events and save_events[-1]["actor"] == "system"


# ================================================================== #
# memory_update honours proposal mode                                   #
# ================================================================== #

class TestUpdateProposalRedirect:
    def test_update_redirected_to_proposal(self, tmp_client, tmp_path):
        from bobo_memory.core.policy import LayerPolicy
        r = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "locked", "content": "v1",
        })
        assert r["ok"]
        tmp_client.policy.layers["auto"] = LayerPolicy(write_mode="proposal")

        r2 = tmp_client.dispatch_tool_call("memory_update", {
            "file": r["file"], "content": "v2",
        })
        assert r2["ok"]
        assert r2.get("proposal") is True
        # Original file untouched
        assert "v1" in (tmp_path / r["file"]).read_text(encoding="utf-8")


# ================================================================== #
# Frontmatter robustness                                                #
# ================================================================== #

class TestFrontmatter:
    def test_created_date_preserved_on_overwrite(self, tmp_client, tmp_path):
        r = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "keep-created", "content": "v1",
        })
        f = tmp_path / r["file"]
        text = f.read_text(encoding="utf-8")
        created_line = next(l for l in text.splitlines() if l.startswith("created:"))

        # Backdate the created field, then overwrite the memory
        f.write_text(text.replace(created_line, "created: 2020-01-01"), encoding="utf-8")
        r2 = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "keep-created", "content": "v2",
        })
        assert r2["ok"]
        new_text = f.read_text(encoding="utf-8")
        assert "created: 2020-01-01" in new_text
        assert "v2" in new_text

    def test_yaml_unsafe_values_do_not_break_index(self, tmp_client, tmp_path):
        r = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "weird",
            "content": "body",
            "summary": "line1\nline2 \"quoted\"",
            "tags": ["a,b", "[x]"],
            "sources": ["src\nwith newline"],
        })
        assert r["ok"], r
        index = (tmp_path / ".bobo" / "memory" / "auto" / "MEMORY.md").read_text(encoding="utf-8")
        weird_lines = [l for l in index.splitlines() if "weird.md" in l]
        assert len(weird_lines) == 1, "summary must stay on one index line"

    def test_utf8_chinese_roundtrip(self, tmp_client, tmp_path):
        content = "# 预算约束\n\n第三季度预算上限为 ¥350,000。表情：🚀"
        r = tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "chinese-content", "content": content,
            "summary": "中文摘要",
        })
        assert r["ok"], r
        read = tmp_client.dispatch_tool_call("memory_read", {"file": r["file"]})
        assert read["ok"]
        assert "¥350,000" in read["content"]
        assert "🚀" in read["content"]


# ================================================================== #
# Proposal pipeline                                                     #
# ================================================================== #

class TestProposalPipeline:
    def _proposal_client(self, tmp_path) -> MemoryClient:
        from bobo_memory.core.policy import LayerPolicy
        client = MemoryClient(project_root=tmp_path, agent_type="prop-agent", scope="project")
        client.policy.layers["auto"] = LayerPolicy(write_mode="proposal")
        return client

    def test_quotes_in_summary_survive_roundtrip(self, tmp_path):
        from bobo_memory.tools.proposal import accept_proposal, list_proposals
        client = self._proposal_client(tmp_path)
        r = client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "tricky",
            "content": "proposal body",
            "summary": 'He said "hello: world" #yes',
        })
        assert r["ok"] and r.get("proposal") is True

        pending = list_proposals(tmp_path)
        assert len(pending) == 1
        assert pending[0]["summary"] == 'He said "hello: world" #yes'

        result = accept_proposal(tmp_path, r["proposal_id"], client)
        assert result["ok"], result
        assert (tmp_path / result["written_to"]).exists()

    def test_accept_blocked_when_secret_in_content(self, tmp_path):
        """A proposal containing a secret must not be merged even by a human."""
        from bobo_memory.tools.proposal import write_proposal, accept_proposal
        client = self._proposal_client(tmp_path)
        r = write_proposal(
            client=client, layer="auto", topic="leak", filename="leak.md",
            content="api_key = 'ABCDEFGHIJKLMNOPQRSTUVWX'",
            summary="s", tags=[], sources=[],
        )
        assert r["ok"]
        result = accept_proposal(tmp_path, r["proposal_id"], client)
        assert not result["ok"]
        assert "policy" in result["error"]


# ================================================================== #
# Recall quality                                                        #
# ================================================================== #

class TestRecallQuality:
    def test_zero_score_docs_filtered(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "python-stack",
            "content": "We use Python and FastAPI.",
            "summary": "Tech stack", "tags": ["python"],
        })
        pack = tmp_client.recall("zzzz qqqq unrelated gibberish", layers=["auto"])
        assert pack.files == [], "irrelevant queries must not return files"

    def test_relevant_docs_still_returned(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "python-stack",
            "content": "We use Python and FastAPI.",
            "summary": "Python tech stack", "tags": ["python"],
        })
        pack = tmp_client.recall("python", layers=["auto"])
        assert len(pack.files) == 1
        assert pack.files[0].filename == "python-stack.md"


# ================================================================== #
# Ingest lease / ack                                                    #
# ================================================================== #

class TestIngestLease:
    def _ingest_one(self, client, text="# Doc\n\nBody.") -> str:
        result = client.ingest(adapter="markdown", payload=text)
        assert result["ok"] and result["docs"]
        return result["docs"][0]["source_id"]

    def test_task_leased_not_deleted(self, tmp_client):
        from bobo_memory.stream.inbox import list_tasks
        sid = self._ingest_one(tmp_client)
        r = tmp_client.dispatch_tool_call("ingest_next", {})
        assert r["ok"] and r["task"]["source_id"] == sid
        assert "ingest_done" in r["instruction"]

        tasks = list_tasks(tmp_client.project_root)
        assert len(tasks) == 1
        assert tasks[0]["status"] == "in_progress"
        assert tasks[0]["attempts"] == 1

    def test_leased_task_not_double_dispatched(self, tmp_client):
        self._ingest_one(tmp_client)
        assert tmp_client.dispatch_tool_call("ingest_next", {})["task"] is not None
        # Within the lease window the same task must not be handed out again
        assert tmp_client.dispatch_tool_call("ingest_next", {})["task"] is None

    def test_expired_lease_requeues(self, tmp_client):
        from datetime import datetime, timedelta, timezone
        from bobo_memory.stream.inbox import _load_tasks, _save_tasks
        from bobo_memory.core.paths import staging_path

        sid = self._ingest_one(tmp_client)
        assert tmp_client.dispatch_tool_call("ingest_next", {})["task"] is not None

        # Backdate the lease past expiry
        staging = staging_path(tmp_client.project_root)
        tasks = _load_tasks(staging)
        tasks[0]["leased_at"] = (
            datetime.now(tz=timezone.utc) - timedelta(minutes=999)
        ).isoformat()
        _save_tasks(staging, tasks)

        r = tmp_client.dispatch_tool_call("ingest_next", {})
        assert r["task"] is not None and r["task"]["source_id"] == sid
        assert r["task"]["attempts"] == 2

    def test_ingest_done_removes_task(self, tmp_client):
        from bobo_memory.stream.inbox import list_tasks
        sid = self._ingest_one(tmp_client)
        tmp_client.dispatch_tool_call("ingest_next", {})
        r = tmp_client.dispatch_tool_call("ingest_done", {"source_id": sid})
        assert r["ok"] and r["completed"] == sid
        assert list_tasks(tmp_client.project_root) == []

    def test_ingest_done_unknown_id_errors(self, tmp_client):
        r = tmp_client.dispatch_tool_call("ingest_done", {"source_id": "nope"})
        assert not r["ok"]

    def test_max_attempts_marks_failed(self, tmp_client):
        from datetime import datetime, timedelta, timezone
        from bobo_memory.stream.inbox import _load_tasks, _save_tasks, list_tasks
        from bobo_memory.core.paths import staging_path

        tmp_client.policy.staging.max_attempts = 2
        self._ingest_one(tmp_client)
        staging = staging_path(tmp_client.project_root)

        def _expire():
            tasks = _load_tasks(staging)
            tasks[0]["leased_at"] = (
                datetime.now(tz=timezone.utc) - timedelta(minutes=999)
            ).isoformat()
            _save_tasks(staging, tasks)

        assert tmp_client.dispatch_tool_call("ingest_next", {})["task"] is not None  # attempt 1
        _expire()
        assert tmp_client.dispatch_tool_call("ingest_next", {})["task"] is not None  # attempt 2
        _expire()
        # attempts exhausted → marked failed, not dispatched, kept for inspection
        assert tmp_client.dispatch_tool_call("ingest_next", {})["task"] is None
        tasks = list_tasks(tmp_client.project_root)
        assert tasks and tasks[0]["status"] == "failed"

    def test_staging_policy_from_dict(self):
        from bobo_memory.core.policy import MemoryPolicy
        p = MemoryPolicy.from_dict({"staging": {"lease_minutes": 5, "max_attempts": 7}})
        assert p.staging.lease_minutes == 5
        assert p.staging.max_attempts == 7
        assert MemoryPolicy.default().staging.lease_minutes == 30


# ================================================================== #
# Lock centralisation                                                   #
# ================================================================== #

class TestLockCentralisation:
    def test_lock_lives_in_bobo_locks(self, tmp_path):
        from bobo_memory.core.atomic import _lock_path_for, file_lock
        target = tmp_path / ".bobo" / "memory" / "auto" / "x.md"
        lock_path = _lock_path_for(target)
        assert lock_path.parent == tmp_path / ".bobo" / "locks"
        with file_lock(target):
            assert lock_path.exists()
        # No .lock files anywhere in memory dirs
        assert list((tmp_path / ".bobo" / "memory").rglob("*.lock")) == []

    def test_same_target_same_lock(self, tmp_path):
        from bobo_memory.core.atomic import _lock_path_for
        a = _lock_path_for(tmp_path / ".bobo" / "memory" / "auto" / "x.md")
        b = _lock_path_for(tmp_path / ".bobo" / "memory" / "auto" / ".." / "auto" / "x.md")
        assert a == b, "path normalisation must map to one lock"

    def test_outside_bobo_keeps_legacy_lock(self, tmp_path):
        from bobo_memory.core.atomic import _lock_path_for
        assert _lock_path_for(tmp_path / "plain.md").name == "plain.md.lock"

    def test_janitor_sweeps_stale_locks(self, tmp_path):
        import os
        import time
        from bobo_memory.core.janitor import cleanup_locks

        client = MemoryClient(project_root=tmp_path, agent_type="lk", scope="project")
        stale_central = tmp_path / ".bobo" / "locks" / "deadbeef.lock"
        stale_legacy = tmp_path / ".bobo" / "memory" / "auto" / "old.md.lock"
        for f in (stale_central, stale_legacy):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("", encoding="utf-8")
            old = time.time() - 2 * 86400
            os.utime(f, (old, old))
        fresh = tmp_path / ".bobo" / "locks" / "fresh.lock"
        fresh.write_text("", encoding="utf-8")

        report = cleanup_locks(tmp_path)
        assert report["deleted_files"] == 2
        assert not stale_central.exists() and not stale_legacy.exists()
        assert fresh.exists(), "fresh locks must be kept"

    def test_run_janitor_includes_locks(self, tmp_path):
        client = MemoryClient(project_root=tmp_path, agent_type="lk", scope="project")
        report = client.run_janitor()
        assert "locks" in report


# ================================================================== #
# Atomic write durability                                               #
# ================================================================== #

class TestAtomicWrite:
    def test_utf8_and_no_tmp_leftovers(self, tmp_path):
        from bobo_memory.core.atomic import atomic_write
        f = tmp_path / "durable.md"
        atomic_write(f, "中文内容 with mixed ASCII ✓")
        assert f.read_text(encoding="utf-8") == "中文内容 with mixed ASCII ✓"
        leftovers = list(tmp_path.glob(".bobo_tmp_*"))
        assert leftovers == []

    def test_overwrite_is_atomic(self, tmp_path):
        from bobo_memory.core.atomic import atomic_write
        f = tmp_path / "swap.md"
        atomic_write(f, "old")
        atomic_write(f, "new")
        assert f.read_text(encoding="utf-8") == "new"
