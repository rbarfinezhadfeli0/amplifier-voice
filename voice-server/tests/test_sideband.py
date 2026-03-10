"""Tests for VoiceSideband — server-side WebSocket to OpenAI RT session."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voice_server.sideband import (
    OPENAI_REALTIME_WS,
    TOOL_CALL_EVENT,
    VoiceSideband,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Yields pre-loaded messages, records sent messages."""

    def __init__(self, messages: list[str] | None = None):
        self._messages = messages or []
        self.sent: list[str] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def make_sideband(
    messages: list[str] | None = None,
    api_key: str = "sk-test-key",
    call_id: str = "call_abc123",
) -> tuple[VoiceSideband, AsyncMock, FakeWebSocket]:
    """Factory: returns (sideband, mock_bridge, fake_ws)."""
    bridge = AsyncMock()
    bridge.execute_tool = AsyncMock()
    fake_ws = FakeWebSocket(messages)
    sb = VoiceSideband(bridge=bridge, api_key=api_key, call_id=call_id)
    return sb, bridge, fake_ws


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVoiceSidebandInit:
    """VoiceSideband.__init__ and _build_ws_url."""

    def test_builds_correct_ws_url(self):
        sb, _, _ = make_sideband(call_id="call_xyz")
        url = sb._build_ws_url()
        assert url == f"{OPENAI_REALTIME_WS}?call_id=call_xyz"

    def test_initial_state(self):
        sb, _, _ = make_sideband()
        assert sb.is_connected is False
        assert sb._ws is None
        assert sb._listen_task is None


class TestVoiceSidebandConnect:
    """VoiceSideband.connect lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_sets_connected_and_starts_listener(self):
        sb, bridge, fake_ws = make_sideband()

        mock_connect_cm = AsyncMock()
        mock_connect_cm.__aenter__ = AsyncMock(return_value=fake_ws)
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "voice_server.sideband.websockets.connect",
            return_value=mock_connect_cm,
        ):
            await sb.connect()

        assert sb.is_connected is True
        assert sb._listen_task is not None

        # Clean up
        await sb.disconnect()


class TestVoiceSidebandToolCall:
    """Tool call events route to bridge and inject result back."""

    @pytest.mark.asyncio
    async def test_tool_call_routes_to_bridge_and_injects_result(self):
        tool_event = {
            "type": TOOL_CALL_EVENT,
            "call_id": "tool_call_1",
            "name": "delegate",
            "arguments": json.dumps({"instruction": "explore codebase"}),
        }

        sb, bridge, fake_ws = make_sideband(messages=[json.dumps(tool_event)])

        # Mock bridge.execute_tool to return a ToolResult-like object
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = {"response": "Found 42 files"}
        mock_result.to_dict = MagicMock(
            return_value={"success": True, "output": {"response": "Found 42 files"}}
        )
        bridge.execute_tool.return_value = mock_result

        mock_connect_cm = AsyncMock()
        mock_connect_cm.__aenter__ = AsyncMock(return_value=fake_ws)
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "voice_server.sideband.websockets.connect",
            return_value=mock_connect_cm,
        ):
            await sb.connect()

        # Give the listen loop time to process
        await asyncio.sleep(0.1)

        # Bridge should have been called with the tool name and parsed arguments
        bridge.execute_tool.assert_awaited_once_with(
            "delegate", {"instruction": "explore codebase"}
        )

        # Should have sent conversation.item.create and response.create back
        sent_messages = [json.loads(m) for m in fake_ws.sent]
        types = [m["type"] for m in sent_messages]
        assert "conversation.item.create" in types
        assert "response.create" in types

        # Clean up
        await sb.disconnect()


class TestVoiceSidebandInjectResult:
    """inject_result sends item_create + response_create."""

    @pytest.mark.asyncio
    async def test_inject_result_sends_item_create_and_response_create(self):
        sb, bridge, fake_ws = make_sideband()

        # Manually wire up connected state with fake ws
        sb._ws = fake_ws
        sb.is_connected = True

        await sb.inject_result(
            call_id="tool_call_99",
            output='{"status": "done"}',
            instructions="dispatched task just completed",
        )

        sent_messages = [json.loads(m) for m in fake_ws.sent]
        types = [m["type"] for m in sent_messages]

        assert "conversation.item.create" in types
        assert "response.create" in types

        # Verify the item_create contains the output
        item_msg = next(
            m for m in sent_messages if m["type"] == "conversation.item.create"
        )
        assert item_msg["item"]["type"] == "function_call_output"
        assert item_msg["item"]["call_id"] == "tool_call_99"

        # Verify response.create contains the instructions
        resp_msg = next(m for m in sent_messages if m["type"] == "response.create")
        assert "dispatched task just completed" in resp_msg["response"]["instructions"]

    @pytest.mark.asyncio
    async def test_inject_result_noop_when_disconnected(self):
        sb, bridge, fake_ws = make_sideband()
        # sb is disconnected by default (is_connected=False)

        await sb.inject_result(
            call_id="tool_call_99",
            output='{"status": "done"}',
            instructions="dispatched task just completed",
        )

        # Nothing should have been sent
        assert len(fake_ws.sent) == 0


class TestVoiceSidebandSendSessionUpdate:
    """send_session_update sends session.update message."""

    @pytest.mark.asyncio
    async def test_send_session_update(self):
        sb, bridge, fake_ws = make_sideband()

        # Manually wire up connected state
        sb._ws = fake_ws
        sb.is_connected = True

        session_config = {"voice": "alloy", "temperature": 0.8}
        await sb.send_session_update(session_config)

        assert len(fake_ws.sent) == 1
        msg = json.loads(fake_ws.sent[0])
        assert msg["type"] == "session.update"
        assert msg["session"] == session_config


class TestVoiceSidebandDispatchRouting:
    """Dispatch tool calls return a synthetic ack and spawn a background task."""

    @pytest.mark.asyncio
    async def test_dispatch_returns_synthetic_ack_and_spawns_task(self):
        dispatch_event = {
            "type": TOOL_CALL_EVENT,
            "call_id": "tool_call_dispatch_1",
            "name": "dispatch",
            "arguments": json.dumps(
                {"agent": "code-reviewer", "instruction": "Review the PR"}
            ),
        }

        sb, bridge, fake_ws = make_sideband(messages=[json.dumps(dispatch_event)])
        bridge.execute_tool.return_value = {"status": "completed"}

        mock_connect_cm = AsyncMock()
        mock_connect_cm.__aenter__ = AsyncMock(return_value=fake_ws)
        mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

        created_tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def spy_create_task(coro, **kwargs):
            task = real_create_task(coro, **kwargs)
            created_tasks.append(task)
            return task

        with patch("asyncio.create_task", side_effect=spy_create_task):
            with patch(
                "voice_server.sideband.websockets.connect",
                return_value=mock_connect_cm,
            ):
                await sb.connect()

            # Give the listen loop time to process the dispatch event
            await asyncio.sleep(0.1)

        # Synthetic ack (conversation.item.create + response.create) must be sent
        sent_messages = [json.loads(m) for m in fake_ws.sent]
        types = [m["type"] for m in sent_messages]
        assert "conversation.item.create" in types
        assert "response.create" in types

        # Ack output must mention "Dispatched to code-reviewer"
        item_msg = next(
            m for m in sent_messages if m["type"] == "conversation.item.create"
        )
        assert "Dispatched" in item_msg["item"]["output"]
        assert "code-reviewer" in item_msg["item"]["output"]

        # asyncio.create_task must have been called at least twice:
        #   1. for the listen loop (inside connect())
        #   2. for the background dispatch job
        assert len(created_tasks) >= 2

        # Clean up background tasks
        for task in created_tasks:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await sb.disconnect()


class TestVoiceSidebandDisconnect:
    """disconnect cleans up state."""

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        sb, bridge, fake_ws = make_sideband()

        # Simulate connected state
        sb._ws = fake_ws
        sb.is_connected = True
        sb._listen_task = asyncio.create_task(asyncio.sleep(10))

        await sb.disconnect()

        assert sb.is_connected is False
        assert sb._ws is None
        assert sb._listen_task is None
