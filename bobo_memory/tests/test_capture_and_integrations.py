"""
Tests for prompt structure, piggyback auto-capture, the MCP server wiring,
and the viewer's read-only REST API.
"""

from __future__ import annotations

import pytest

from bobo_memory import MemoryClient


@pytest.fixture
def tmp_client(tmp_path):
    return MemoryClient(project_root=tmp_path, agent_type="cap-agent", scope="project")


# ================================================================== #
# Prompt structure                                                     #
# ================================================================== #

class TestPromptStructure:
    def test_instructions_injected_once(self, tmp_client):
        prompt = tmp_client.build_system_prompt("Base.")
        assert prompt.count("## How to save memories") == 1
        assert prompt.count("## When to save") == 1
        assert prompt.count("## What NOT to save") == 1

    def test_prompt_teaches_tool_protocol_not_manual_writes(self, tmp_client):
        prompt = tmp_client.build_system_prompt()
        assert "memory_save" in prompt
        assert "never edit MEMORY.md" in prompt
        assert "two-step protocol" not in prompt

    def test_routing_note_only_with_multiple_layers(self, tmp_path):
        both = MemoryClient(project_root=tmp_path, agent_type="a", scope="project")
        assert "## Choosing a memory layer" in both.build_system_prompt()

        single = MemoryClient(
            project_root=tmp_path, agent_type="a", scope="project",
            enabled_layers=["auto"],
        )
        assert "## Choosing a memory layer" not in single.build_system_prompt()

    def test_each_layer_keeps_dir_and_index(self, tmp_client):
        prompt = tmp_client.build_system_prompt()
        assert "Persistent Agent Memory" in prompt
        assert "Auto Memory" in prompt
        assert prompt.count("## MEMORY.md") == 2

    def test_standalone_layer_prompt_still_complete(self, tmp_client):
        """Direct build_prompt() calls keep full instructions (back-compat)."""
        fragment = tmp_client._auto_mem.build_prompt()
        assert "## How to save memories" in fragment


# ================================================================== #
# Piggyback auto-capture                                               #
# ================================================================== #

class TestMemoryNudge:
    def test_chinese_remember_triggers(self, tmp_client):
        msgs = [{"role": "user", "content": "记住：部署前必须先跑集成测试"}]
        nudge = tmp_client.memory_nudge(msgs)
        assert "memory_save" in nudge
        assert "explicit_remember" in nudge

    def test_english_preference_triggers(self, tmp_client):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "From now on always use pnpm, not npm."},
        ]
        nudge = tmp_client.memory_nudge(msgs)
        assert nudge != ""
        assert "preference" in nudge

    def test_correction_triggers(self, tmp_client):
        msgs = [{"role": "user", "content": "不对，端口应该是 8443 而不是 8080"}]
        assert tmp_client.memory_nudge(msgs) != ""

    def test_plain_chat_does_not_trigger(self, tmp_client):
        msgs = [
            {"role": "user", "content": "今天天气怎么样？"},
            {"role": "assistant", "content": "我看看。"},
            {"role": "user", "content": "帮我算一下 3+4"},
        ]
        assert tmp_client.memory_nudge(msgs) == ""

    def test_cooldown_suppresses_repeat_nudges(self, tmp_client):
        msgs = [{"role": "user", "content": "记住这个约定"}]
        first = tmp_client.memory_nudge(msgs, cooldown_messages=6)
        assert first != ""
        # Two more messages later — still inside the cooldown window
        msgs += [{"role": "assistant", "content": "好的"},
                 {"role": "user", "content": "记住另一个约定"}]
        assert tmp_client.memory_nudge(msgs, cooldown_messages=6) == ""
        # Enough new messages → nudge allowed again
        msgs += [{"role": "assistant", "content": "x"},
                 {"role": "user", "content": "以后都用这个方案"},
                 {"role": "assistant", "content": "x"},
                 {"role": "user", "content": "记住它"}]
        assert tmp_client.memory_nudge(msgs, cooldown_messages=6) != ""

    def test_lookback_ignores_old_messages(self, tmp_client):
        msgs = [{"role": "user", "content": "记住这个"}] + [
            {"role": "user", "content": f"普通消息 {i}"} for i in range(6)
        ]
        assert tmp_client.memory_nudge(msgs, lookback=4) == ""

    def test_structured_content_blocks_supported(self, tmp_client):
        msgs = [{
            "role": "user",
            "content": [{"type": "text", "text": "please remember my API base url"}],
        }]
        assert tmp_client.memory_nudge(msgs) != ""


class TestFindSimilar:
    def test_finds_existing_memory(self, tmp_client):
        tmp_client.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "deploy-steps",
            "content": "Deployment requires integration tests to pass first.",
            "summary": "Deployment integration tests rule",
            "tags": ["deploy"],
        })
        similar = tmp_client.find_similar("integration tests before deployment")
        assert similar and similar[0].filename == "deploy-steps.md"

    def test_no_match_returns_empty(self, tmp_client):
        assert tmp_client.find_similar("完全无关的火星话题") == []


# ================================================================== #
# MCP server (skipped when mcp is not installed)                       #
# ================================================================== #

class TestMcpServer:
    def test_import_error_message_without_mcp(self, tmp_client):
        try:
            import mcp  # noqa: F401
            pytest.skip("mcp installed — error-path not testable")
        except ImportError:
            pass
        from bobo_memory.mcp_server import create_mcp_server
        with pytest.raises(ImportError, match=r"bobo-memory\[mcp\]"):
            create_mcp_server(tmp_client)

    def test_tools_listed_over_mcp(self, tmp_client):
        pytest.importorskip("mcp")
        import anyio
        from bobo_memory.mcp_server import create_mcp_server

        server = create_mcp_server(tmp_client)
        handler = server.request_handlers  # smoke: server built with handlers
        assert handler


# ================================================================== #
# Viewer read-only REST API                                            #
# ================================================================== #

class TestViewerApi:
    @pytest.fixture
    def api(self, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient as HttpClient
        from bobo_memory.viewer.app import create_app

        mem = MemoryClient(project_root=tmp_path, agent_type="default", scope="project")
        mem.dispatch_tool_call("memory_save", {
            "layer": "auto", "topic": "api-test",
            "content": "Viewer API test content.",
            "summary": "Viewer test",
        })
        return HttpClient(create_app(str(tmp_path)))

    def test_layers_endpoint(self, api):
        r = api.get("/layers")
        assert r.status_code == 200
        assert "auto" in r.json()["enabled_layers"]

    def test_memories_endpoint(self, api):
        r = api.get("/memories/auto")
        assert r.status_code == 200
        assert "api-test.md" in r.json()["index"]

    def test_memories_unknown_layer_400(self, api):
        assert api.get("/memories/nope").status_code == 400

    def test_memory_read_endpoint(self, api):
        r = api.get("/memory", params={"file": ".bobo/memory/auto/api-test.md"})
        assert r.status_code == 200
        assert "Viewer API test content" in r.json()["content"]

    def test_memory_read_missing_404(self, api):
        r = api.get("/memory", params={"file": ".bobo/memory/auto/ghost.md"})
        assert r.status_code == 404

    def test_memory_read_traversal_blocked(self, api):
        r = api.get("/memory", params={"file": "../outside.md"})
        assert r.status_code == 400

    def test_recall_endpoint(self, api):
        r = api.get("/recall", params={"query": "viewer api test"})
        assert r.status_code == 200
        files = r.json()["files"]
        assert files and files[0]["filename"] == "api-test.md"

    def test_storage_endpoint(self, api):
        r = api.get("/storage")
        assert r.status_code == 200
        assert "layers" in r.json()
