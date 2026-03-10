"""
VoiceSideband — server-side WebSocket to an OpenAI Realtime session.

Opens a parallel WebSocket connection to the *same* OpenAI Realtime session
that the browser is using (identified by ``call_id``).  This lets the server
inject tool results, session updates, and other events without routing
everything through the browser.
"""

import asyncio
import json
import logging
from typing import Any

import websockets

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OPENAI_REALTIME_WS = "wss://api.openai.com/v1/realtime"
TOOL_CALL_EVENT = "response.function_call_arguments.done"

# ---------------------------------------------------------------------------
# Module-level lock — serialises delegate / dispatch calls sharing one session
# ---------------------------------------------------------------------------
execution_lock = asyncio.Lock()


class VoiceSideband:
    """Server-side WebSocket that shadows an OpenAI Realtime session."""

    def __init__(self, bridge: Any, api_key: str, call_id: str) -> None:
        self._bridge = bridge
        self._api_key = api_key
        self.call_id = call_id

        self._ws: Any = None
        self._listen_task: asyncio.Task[None] | None = None
        self.is_connected: bool = False

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _build_ws_url(self) -> str:
        return f"{OPENAI_REALTIME_WS}?call_id={self.call_id}"

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the sideband WebSocket and start the listener."""
        url = self._build_ws_url()
        headers = {"Authorization": f"Bearer {self._api_key}"}

        logger.info("Sideband connecting to %s", url)
        self._ws = await websockets.connect(
            url, additional_headers=headers
        ).__aenter__()
        self.is_connected = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("Sideband connected")

    async def _listen_loop(self) -> None:
        """Read messages from the sideband WebSocket until closed."""
        try:
            async for raw_msg in self._ws:
                try:
                    event = json.loads(raw_msg)
                    await self._handle_event(event)
                except json.JSONDecodeError:
                    logger.warning("Sideband received non-JSON message")
        except websockets.ConnectionClosed:
            logger.info("Sideband WebSocket closed by server")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Sideband listen loop error")

    async def _handle_event(self, event: dict) -> None:
        """Route an incoming event to the appropriate handler."""
        event_type = event.get("type", "")
        if event_type == TOOL_CALL_EVENT:
            await self._handle_tool_call(event)
        elif event_type.startswith("error"):
            logger.error("Sideband received error event: %s", event)

    # ------------------------------------------------------------------
    # Tool-call handling
    # ------------------------------------------------------------------

    async def _handle_tool_call(self, event: dict) -> None:
        """Route an incoming tool-call event to the appropriate handler."""
        tool_name = event.get("name", "")
        call_id = event.get("call_id", "")
        raw_args = event.get("arguments", "{}")

        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            arguments = {}

        logger.info("Sideband tool call: %s (call_id=%s)", tool_name, call_id)

        if tool_name == "dispatch":
            await self._handle_dispatch(call_id, arguments)
        elif tool_name == "cancel_current_task":
            await self._handle_cancel(call_id, arguments)
        else:
            await self._handle_delegate(call_id, tool_name, arguments)

    async def _handle_delegate(self, call_id: str, name: str, arguments: dict) -> None:
        """Execute a tool call synchronously (serialised) and inject the result."""
        async with execution_lock:
            result = await self._bridge.execute_tool(name, arguments)

        if hasattr(result, "to_dict"):
            output = json.dumps(result.to_dict())
        else:
            output = json.dumps(result)

        item_create = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            },
        }
        response_create = {
            "type": "response.create",
            "response": {
                "instructions": "Process the tool result and respond to the user.",
            },
        }

        await self._ws.send(json.dumps(item_create))
        await self._ws.send(json.dumps(response_create))

    async def _handle_dispatch(self, call_id: str, arguments: dict) -> None:
        """Send an immediate synthetic ack and spawn a background agent job."""
        agent = arguments.get("agent", "unknown")
        instruction = arguments.get("instruction", "")

        ack_output = f"Dispatched to {agent}. You'll be notified when complete."
        item_create = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": ack_output,
            },
        }
        response_create = {
            "type": "response.create",
            "response": {
                "instructions": (
                    "Acknowledge that the task has been dispatched and is running "
                    "in the background."
                ),
            },
        }

        await self._ws.send(json.dumps(item_create))
        await self._ws.send(json.dumps(response_create))

        asyncio.create_task(
            run_agent_job(
                sideband=self,
                call_id=call_id,
                agent=agent,
                instruction=instruction,
                bridge=self._bridge,
            )
        )

    async def _handle_cancel(self, call_id: str, arguments: dict) -> None:
        """Route a cancel_current_task call through the bridge."""
        result = await self._bridge.execute_tool("cancel_current_task", arguments)

        if hasattr(result, "to_dict"):
            output = json.dumps(result.to_dict())
        else:
            output = json.dumps(result)

        item_create = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            },
        }
        response_create = {
            "type": "response.create",
            "response": {
                "instructions": "Process the tool result and respond to the user.",
            },
        }

        await self._ws.send(json.dumps(item_create))
        await self._ws.send(json.dumps(response_create))

    # ------------------------------------------------------------------
    # Async result injection (for dispatched / background tasks)
    # ------------------------------------------------------------------

    async def inject_result(self, call_id: str, output: str, instructions: str) -> None:
        """Inject a completed async result into the Realtime session.

        No-op when disconnected.
        """
        if not self.is_connected or self._ws is None:
            return

        item_create = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            },
        }
        response_create = {
            "type": "response.create",
            "response": {
                "instructions": instructions,
            },
        }

        await self._ws.send(json.dumps(item_create))
        await self._ws.send(json.dumps(response_create))

    # ------------------------------------------------------------------
    # Session updates
    # ------------------------------------------------------------------

    async def send_session_update(self, session_config: dict) -> None:
        """Send a session.update message to the Realtime session."""
        msg = {
            "type": "session.update",
            "session": session_config,
        }
        await self._ws.send(json.dumps(msg))

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        """Cancel listener, close WebSocket, reset state."""
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self.is_connected = False
        logger.info("Sideband disconnected")


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------


async def run_agent_job(
    sideband: VoiceSideband,
    call_id: str,
    agent: str,
    instruction: str,
    bridge: Any,
) -> None:
    """Run a delegate call in the background and inject the result into the session.

    Acquires ``execution_lock`` for the duration of the ``bridge.execute_tool``
    call so that concurrent delegate / dispatch invocations sharing the same
    Amplifier session are serialised.

    On success the tool output is injected via :meth:`VoiceSideband.inject_result`.
    On any exception a human-readable error string is injected instead — the
    function itself never re-raises.
    """
    try:
        async with execution_lock:
            result = await bridge.execute_tool(
                "delegate", {"agent": agent, "instruction": instruction}
            )

        if hasattr(result, "to_dict"):
            output = json.dumps(result.to_dict())
        else:
            output = json.dumps(result)

        await sideband.inject_result(
            call_id=call_id,
            output=output,
            instructions="The background agent task has completed. Summarize the result to the user.",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("run_agent_job failed for call_id=%s", call_id)
        await sideband.inject_result(
            call_id=call_id,
            output=f"Background task failed: {e}",
            instructions="A background task encountered an error. Inform the user.",
        )
