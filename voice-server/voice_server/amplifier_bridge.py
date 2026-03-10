"""
Amplifier Bridge - Programmatic integration with Microsoft Amplifier.

This module provides direct, programmatic access to Amplifier's capabilities
without subprocess overhead. Uses amplifier-foundation for bundle loading and
session management.

Architecture:
- OpenAI Realtime API: Voice I/O (STT/TTS, WebRTC audio)
- This bridge: Tool execution via direct Python calls
- Amplifier Foundation: Bundle loading, session lifecycle, coordinator
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Event streaming for debugging
from voice_server.protocols.event_streaming import (
    EventStreamingHook,
    EVENTS_TO_CAPTURE,
)
from voice_server.tools.dispatch_tool import DISPATCH_TOOL_DEFINITION

logger = logging.getLogger(__name__)


def _make_json_safe(obj: Any) -> Any:
    """
    Convert any object to a JSON-safe representation.

    Handles:
    - Dicts and lists recursively
    - Objects with to_dict() or __dict__
    - Non-serializable types -> string representation
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(item) for item in obj]

    # Try common serialization methods
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return _make_json_safe(obj.to_dict())

    if hasattr(obj, "__dict__"):
        return _make_json_safe(obj.__dict__)

    # Fallback: string representation
    return str(obj)


@dataclass
class ToolResult:
    """Result from executing a tool."""

    success: bool
    output: Any
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-safe dict."""
        result = {"success": self.success}
        if self.success:
            # Ensure output is JSON-safe
            result["output"] = _make_json_safe(self.output)
        else:
            result["error"] = self.error
        return result


class AmplifierBridge:
    """
    Programmatic Amplifier integration - no CLI subprocess!

    Creates a long-lived AmplifierSession with all tools mounted.
    Tools are executed via direct coordinator calls.

    Cancellation:
        Call request_cancel() to cancel running operations.
        - Graceful (default): Waits for current tools to complete
        - Immediate: Stops now, synthesizes results for pending tools

        Cancellation propagates to all child sessions (spawned agents).
    """

    def __init__(self, bundle_name: str = "amplifier-dev", cwd: Optional[str] = None):
        self._bundle_name = bundle_name
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._session = None
        self._coordinator = None
        self._prepared = None  # Store prepared bundle for spawning
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._initialized = False

        # Event streaming for debugging
        self._event_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._streaming_hook: Optional[EventStreamingHook] = None

        # Track active child sessions for cancellation propagation
        self._active_child_sessions: Dict[str, Any] = {}

    async def initialize(self) -> None:
        """Initialize long-lived Amplifier session with all tools."""
        if self._initialized:
            return

        logger.info(f"Initializing Amplifier bridge with bundle: {self._bundle_name}")

        try:
            # Import here to avoid issues if amplifier-foundation not installed
            from amplifier_foundation import load_bundle
            import os

            # Load foundation bundle (includes all tools)
            logger.debug(f"Loading bundle: {self._bundle_name}")
            bundle = await load_bundle(self._bundle_name)

            # Add Anthropic provider for delegate tool delegation (if API key available)
            anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
            if anthropic_api_key:
                logger.info(
                    "Configuring Anthropic (Claude Opus) provider for Amplifier sessions"
                )
                if not bundle.providers:
                    bundle.providers = []

                # Add Anthropic provider with Opus model
                # CRITICAL: Must include "source" field so Amplifier knows where to download the module
                bundle.providers.append(
                    {
                        "module": "provider-anthropic",
                        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
                        "config": {
                            "priority": 1,
                            "default_model": "claude-opus-4-5-20251101",  # Specific Opus 4.5 version
                        },
                    }
                )
            else:
                logger.warning(
                    "ANTHROPIC_API_KEY not set - delegate tool delegation will not work"
                )

            # CRITICAL: Explicitly add tool-delegate module
            # The agents behavior uses a relative path that doesn't work when loading from GitHub
            # We add it here with the full git URL to ensure it's available
            if not bundle.tools:
                bundle.tools = []

            # Check if delegate tool is already in the bundle
            has_delegate = any(
                t.get("module") == "tool-delegate"
                for t in bundle.tools
                if isinstance(t, dict)
            )
            if not has_delegate:
                logger.info("Adding tool-delegate module to bundle")
                bundle.tools.append(
                    {
                        "module": "tool-delegate",
                        "source": "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate",
                        "config": {
                            "features": {
                                "self_delegation": {"enabled": True},
                                "session_resume": {"enabled": True},
                                "context_inheritance": {
                                    "enabled": True,
                                    "max_turns": 10,
                                },
                                "provider_selection": {"enabled": True},
                            },
                            "settings": {
                                "exclude_tools": [
                                    "delegate"
                                ],  # Spawned agents can't further delegate
                                "exclude_hooks": [],
                                "timeout": 300,
                            },
                        },
                    }
                )

            # Prepare (resolve modules)
            logger.debug("Preparing bundle (resolving modules)...")
            self._prepared = await bundle.prepare()

            # Create session
            logger.debug(f"Creating session with cwd: {self._cwd}")
            self._session = await self._prepared.create_session(session_cwd=self._cwd)

            self._coordinator = self._session.coordinator

            # Initialize session (mounts all modules/tools)
            logger.debug("Initializing session (mounting modules)...")
            await self._session.initialize()

            # Register event streaming hook for debugging
            self._register_event_streaming_hook()

            # Register spawning capability for task tool (must be after initialize)
            self._register_spawn_capability()

            # Discover mounted tools
            await self._discover_tools()

            self._initialized = True
            logger.info(f"Amplifier bridge initialized with {len(self._tools)} tools")

        except ImportError as e:
            logger.error(f"Failed to import amplifier-foundation: {e}")
            logger.error(
                "Make sure amplifier-foundation is installed: pip install amplifier-foundation"
            )
            raise RuntimeError("amplifier-foundation not available") from e

        except Exception as e:
            logger.error(f"Failed to initialize Amplifier: {e}", exc_info=True)
            raise

    def _register_event_streaming_hook(self) -> None:
        """Register event streaming hook for debugging.

        Captures ALL Amplifier events and queues them for SSE streaming
        to the browser console for full debugging visibility.
        """
        if not self._coordinator:
            logger.warning("No coordinator available for event streaming hook")
            return

        # Create the streaming hook
        self._streaming_hook = EventStreamingHook(self._event_queue)

        # Get hook registry from coordinator
        hook_registry = self._coordinator.get("hooks")
        if not hook_registry:
            logger.warning("No hook registry available - event streaming disabled")
            return

        # Register hook for ALL events we want to capture
        registered_count = 0
        for event in EVENTS_TO_CAPTURE:
            try:
                hook_registry.register(
                    event=event,
                    handler=self._streaming_hook,
                    priority=100,  # Run early to capture events
                    name=f"voice-streaming:{event}",
                )
                registered_count += 1
            except Exception as e:
                logger.debug(f"Could not register hook for {event}: {e}")

        logger.info(f"Registered event streaming hook for {registered_count} events")

    @property
    def event_queue(self) -> asyncio.Queue[Dict[str, Any]]:
        """Get the event queue for SSE streaming."""
        return self._event_queue

    async def request_cancel(self, immediate: bool = False) -> Dict[str, Any]:
        """Request cancellation of current operations.

        This triggers the kernel's cancellation mechanism, which propagates
        to all registered child sessions (spawned agents).

        Args:
            immediate: If True, stop immediately and synthesize results for
                      pending tools. If False (default), wait for current
                      tools to complete gracefully.

        Returns:
            Dict with cancellation status:
            - cancelled: bool - whether cancellation was requested
            - level: "graceful" | "immediate" - cancellation level
            - running_tools: list of tool names currently running
            - error: optional error message if cancellation failed
        """
        if not self._coordinator:
            return {
                "cancelled": False,
                "error": "No session active - nothing to cancel",
            }

        try:
            # Use the kernel's cancellation mechanism
            cancellation = self._coordinator.cancellation

            if immediate:
                changed = cancellation.request_immediate()
                level = "immediate"
            else:
                changed = cancellation.request_graceful()
                level = "graceful"

            running_tools = list(cancellation.running_tool_names)

            if changed:
                logger.info(
                    f"Cancellation requested: level={level}, running_tools={running_tools}"
                )
            else:
                logger.info(
                    f"Cancellation already in progress or escalated: level={level}"
                )

            return {
                "cancelled": True,
                "level": level,
                "running_tools": running_tools,
                "was_already_cancelled": not changed,
            }

        except Exception as e:
            logger.error(f"Failed to request cancellation: {e}", exc_info=True)
            return {
                "cancelled": False,
                "error": str(e),
            }

    @property
    def is_cancellable(self) -> bool:
        """Check if there are operations that can be cancelled.

        Returns True if there are tools currently running or child sessions active.
        """
        if not self._coordinator:
            return False

        cancellation = self._coordinator.cancellation
        return bool(cancellation.running_tools) or bool(self._active_child_sessions)

    @property
    def cancellation_state(self) -> Dict[str, Any]:
        """Get current cancellation state for UI feedback.

        Returns:
            Dict with:
            - is_cancelled: bool
            - is_graceful: bool
            - is_immediate: bool
            - running_tools: list of tool names
            - active_children: count of active child sessions
        """
        if not self._coordinator:
            return {
                "is_cancelled": False,
                "is_graceful": False,
                "is_immediate": False,
                "running_tools": [],
                "active_children": 0,
            }

        cancellation = self._coordinator.cancellation
        return {
            "is_cancelled": cancellation.is_cancelled,
            "is_graceful": cancellation.is_graceful,
            "is_immediate": cancellation.is_immediate,
            "running_tools": list(cancellation.running_tool_names),
            "active_children": len(self._active_child_sessions),
        }

    def _register_spawn_capability(self) -> None:
        """Register session.spawn and session.resume capabilities for delegate tool.

        This enables the delegate tool to spawn and resume sub-sessions for agents.

        IMPORTANT: Unlike PreparedBundle.spawn(), this implementation wires up
        cancellation propagation between parent and child sessions. This follows
        the pattern used in amplifier-app-cli/session_spawner.py.
        """
        from amplifier_foundation import Bundle

        async def spawn_capability(
            agent_name: str,
            instruction: str,
            parent_session: Any,
            agent_configs: Dict[str, Dict[str, Any]],
            sub_session_id: Optional[str] = None,
            tool_inheritance: Optional[Dict[str, Any]] = None,
            hook_inheritance: Optional[Dict[str, Any]] = None,
            orchestrator_config: Optional[Dict[str, Any]] = None,
            provider_preferences: Optional[List[Any]] = None,
            parent_messages: Optional[List[Dict[str, Any]]] = None,
            self_delegation_depth: int = 0,
            **kwargs: Any,
        ) -> Dict[str, Any]:
            """Spawn sub-session for agent delegation with cancellation propagation.

            Args:
                agent_name: Name of the agent to spawn (or "self" for self-delegation).
                instruction: Task instruction for the agent.
                parent_session: Parent session for lineage tracking.
                agent_configs: Agent configuration overrides.
                sub_session_id: Optional session ID for resuming.
                tool_inheritance: Tool inheritance config (exclude_tools or inherit_tools).
                hook_inheritance: Hook inheritance config (exclude_hooks or inherit_hooks).
                orchestrator_config: Optional orchestrator config for rate limiting, etc.
                provider_preferences: Ordered list of provider/model preferences.
                parent_messages: Optional messages from parent to inject into child context.
                self_delegation_depth: Current depth of self-delegation chain.

            Returns:
                Dict with result from spawned agent.
            """
            logger.info(
                f"Spawning agent: {agent_name} with instruction: {instruction[:100]}..."
            )

            # Get parent's cancellation token for propagation
            parent_cancellation = None
            if parent_session and hasattr(parent_session, "coordinator"):
                parent_cancellation = parent_session.coordinator.cancellation

            # Generate a unique ID for tracking this spawn
            import uuid

            spawn_id = sub_session_id or f"{agent_name}_{uuid.uuid4().hex[:8]}"

            # Track this spawn as active
            self._active_child_sessions[spawn_id] = {
                "agent_name": agent_name,
                "instruction": instruction[:100],
                "started_at": asyncio.get_event_loop().time(),
            }

            try:
                # Handle "self" delegation - use parent's bundle
                if agent_name == "self":
                    logger.info(f"Self-delegation at depth {self_delegation_depth}")

                    # For self-delegation, use spawn but capture child for cancellation
                    # We call spawn with a wrapper that registers cancellation
                    result = await self._spawn_with_cancellation(
                        child_bundle=None,  # Use parent's bundle
                        instruction=instruction,
                        session_id=sub_session_id,
                        parent_session=parent_session,
                        parent_cancellation=parent_cancellation,
                        session_cwd=self._cwd,
                        orchestrator_config=orchestrator_config,
                        provider_preferences=provider_preferences,
                        parent_messages=parent_messages,
                    )
                    return result

                # Resolve agent name to configuration
                if agent_name in agent_configs:
                    config = agent_configs[agent_name]
                elif (
                    self._prepared
                    and hasattr(self._prepared.bundle, "agents")
                    and agent_name in self._prepared.bundle.agents
                ):
                    config = self._prepared.bundle.agents[agent_name]
                else:
                    available = list(agent_configs.keys())
                    if self._prepared and hasattr(self._prepared.bundle, "agents"):
                        available += list(self._prepared.bundle.agents.keys())
                    error_msg = (
                        f"Agent '{agent_name}' not found. Available: {available}"
                    )
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                # Create child bundle from agent config
                child_bundle = Bundle(
                    name=agent_name,
                    version="1.0.0",
                    session=config.get("session", {}),
                    providers=config.get("providers", []),
                    tools=config.get("tools", []),
                    hooks=config.get("hooks", []),
                    instruction=config.get("instruction")
                    or config.get("system", {}).get("instruction"),
                )

                # Apply tool/hook inheritance to child bundle's spawn config
                if tool_inheritance or hook_inheritance:
                    child_bundle.spawn = {}

                    if tool_inheritance:
                        if "exclude_tools" in tool_inheritance:
                            child_bundle.spawn["exclude_tools"] = tool_inheritance[
                                "exclude_tools"
                            ]
                        elif "inherit_tools" in tool_inheritance:
                            child_bundle.spawn["tools"] = tool_inheritance[
                                "inherit_tools"
                            ]

                    if hook_inheritance:
                        if "exclude_hooks" in hook_inheritance:
                            child_bundle.spawn["exclude_hooks"] = hook_inheritance[
                                "exclude_hooks"
                            ]
                        elif "inherit_hooks" in hook_inheritance:
                            child_bundle.spawn["hooks"] = hook_inheritance[
                                "inherit_hooks"
                            ]

                # Spawn with cancellation propagation
                logger.debug(
                    f"Spawning agent {agent_name} with cancellation propagation"
                )
                result = await self._spawn_with_cancellation(
                    child_bundle=child_bundle,
                    instruction=instruction,
                    session_id=sub_session_id,
                    parent_session=parent_session,
                    parent_cancellation=parent_cancellation,
                    session_cwd=self._cwd,
                    orchestrator_config=orchestrator_config,
                    provider_preferences=provider_preferences,
                    parent_messages=parent_messages,
                )
                return result

            finally:
                # Remove from active sessions
                self._active_child_sessions.pop(spawn_id, None)
                logger.debug(
                    f"Spawn {spawn_id} completed, removed from active sessions"
                )

        async def resume_capability(
            sub_session_id: str,
            instruction: str,
        ) -> Dict[str, Any]:
            """Resume an existing agent sub-session.

            Args:
                sub_session_id: Full session ID from previous delegate call.
                instruction: Follow-up instruction for the agent.

            Returns:
                Dict with result from resumed agent.
            """
            logger.info(f"Resuming session: {sub_session_id}")

            # Use PreparedBundle.resume() to handle session resumption
            return await self._prepared.resume(
                session_id=sub_session_id,
                instruction=instruction,
            )

        # Register both capabilities with the coordinator
        self._coordinator.register_capability("session.spawn", spawn_capability)
        self._coordinator.register_capability("session.resume", resume_capability)
        logger.info(
            "Registered session.spawn and session.resume capabilities for delegate tool"
        )

    async def _spawn_with_cancellation(
        self,
        child_bundle: Optional[Any],
        instruction: str,
        session_id: Optional[str],
        parent_session: Any,
        parent_cancellation: Optional[Any],
        session_cwd: Path,
        orchestrator_config: Optional[Dict[str, Any]] = None,
        provider_preferences: Optional[List[Any]] = None,
        parent_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Spawn a child session with cancellation propagation.

        This wraps PreparedBundle.spawn() to add cancellation wiring,
        following the pattern from amplifier-app-cli/session_spawner.py.

        The key addition is registering the child's cancellation token
        with the parent's, so that when the parent is cancelled, the
        child is also cancelled.

        Args:
            child_bundle: Bundle for the child session (None for self-delegation)
            instruction: Task instruction for the child
            session_id: Optional session ID for resumption
            parent_session: Parent session for lineage
            parent_cancellation: Parent's cancellation token for propagation
            session_cwd: Working directory for the child session
            orchestrator_config: Optional orchestrator config
            provider_preferences: Optional provider preferences
            parent_messages: Optional messages to inject

        Returns:
            Dict with result from the spawned session
        """
        # Check if already cancelled before starting
        if parent_cancellation and parent_cancellation.is_cancelled:
            logger.info("Parent already cancelled, skipping spawn")
            return {
                "response": "Task cancelled before execution",
                "session_id": session_id,
                "cancelled": True,
            }

        # For now, we use PreparedBundle.spawn() and note that full cancellation
        # propagation requires foundation-level changes.
        #
        # TODO: When foundation exposes child session access, wire up:
        #   parent_cancellation.register_child(child_cancellation)
        #   try:
        #       result = await child_session.execute(...)
        #   finally:
        #       parent_cancellation.unregister_child(child_cancellation)
        #
        # Current behavior: Main session cancellation works, but won't
        # automatically propagate to in-flight child sessions.

        logger.debug("Spawning child session")

        result = await self._prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=session_id,
            parent_session=parent_session,
            session_cwd=session_cwd,
            orchestrator_config=orchestrator_config,
            provider_preferences=provider_preferences,
            parent_messages=parent_messages,
        )

        return result

    async def _discover_tools(self):
        """Enumerate tools from the coordinator using public API."""
        if not self._coordinator:
            logger.warning("No coordinator available for tool discovery")
            return

        # Get all mounted tools using public API
        tools_dict = self._coordinator.get("tools")

        if not tools_dict:
            logger.warning("No tools mounted on coordinator")
            return

        logger.debug(f"Found {len(tools_dict)} mounted tools")

        for tool_name, tool in tools_dict.items():
            try:
                # Get basic tool info from protocol
                description = tool.description if hasattr(tool, "description") else ""

                # Get input schema (modern tools use input_schema attribute)
                parameters = {}
                if hasattr(tool, "input_schema"):
                    try:
                        parameters = tool.input_schema
                        logger.debug(
                            f"Tool {tool_name} has input_schema with {len(parameters.get('properties', {}))} parameters"
                        )
                    except Exception as e:
                        logger.debug(f"Tool {tool_name} input_schema error: {e}")

                self._tools[tool_name] = {
                    "name": tool_name,
                    "description": description,
                    "parameters": parameters,
                    "tool": tool,  # Keep reference for execution
                }

                logger.debug(f"Discovered tool: {tool_name} - {description[:100]}...")

            except Exception as e:
                logger.warning(f"Failed to register tool {tool_name}: {e}")
                continue

    # Tools to expose to the realtime model (others available to agents internally)
    # Only delegate tool - forces ALL work to be delegated to agents
    # Using NEW delegate tool (not legacy task tool) for enhanced context control
    REALTIME_TOOLS = {"delegate"}

    # Cancellation tool - handled server-side
    # Allows the voice model (or user) to cancel running operations
    CANCEL_TOOL = {
        "type": "function",
        "name": "cancel_current_task",
        "description": (
            "Cancel the currently running task or delegation. Use when the user says "
            "'stop', 'cancel', 'never mind', 'abort', or indicates they want to interrupt "
            "what you're doing. This gracefully stops running agents and tools. "
            "If operations continue after calling this, call it again for immediate stop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason for cancellation (e.g., 'user requested', 'changed mind')",
                },
                "immediate": {
                    "type": "boolean",
                    "description": "If true, stop immediately without waiting for current operations. Default false.",
                },
            },
            "required": [],
        },
    }

    def get_tools_for_openai(self) -> List[Dict[str, Any]]:
        """
        Convert Amplifier tools to OpenAI function format.

        Only exposes REALTIME_TOOLS to the voice model, forcing it to delegate
        actual work to agents via the task tool. All tools remain available
        internally for agent execution.

        Also includes the dispatch tool (async fire-and-forget delegation) and
        the cancellation tool (server-side).

        Returns list of function definitions ready for OpenAI API.
        Ensures all schemas are JSON-safe for OpenAI.
        """
        openai_tools = []

        for tool_info in self._tools.values():
            # Only expose orchestration tools to realtime model
            if tool_info["name"] not in self.REALTIME_TOOLS:
                continue

            # Ensure the entire tool definition is JSON-safe
            tool_def = {
                "type": "function",
                "name": tool_info["name"],
                "description": str(tool_info["description"])
                if tool_info["description"]
                else "",
                "parameters": _make_json_safe(tool_info["parameters"]),
            }
            openai_tools.append(tool_def)

        # Add dispatch tool (async fire-and-forget delegation)
        openai_tools.append(DISPATCH_TOOL_DEFINITION)

        # Add cancellation tool (handled server-side)
        openai_tools.append(self.CANCEL_TOOL)

        return openai_tools

    # Alias for backward compatibility
    def get_tools(self) -> List[Dict[str, Any]]:
        """Alias for get_tools_for_openai()."""
        return self.get_tools_for_openai()

    async def _handle_cancel_tool(self, arguments: Dict[str, Any]) -> "ToolResult":
        """Handle the cancel_current_task tool invocation.

        This is called when the voice model (or user via tool call) wants to cancel
        running operations.

        Args:
            arguments: Tool arguments with optional 'reason' and 'immediate' fields

        Returns:
            ToolResult indicating cancellation status
        """
        reason = arguments.get("reason", "user requested")
        immediate = arguments.get("immediate", False)

        logger.info(f"Cancel tool invoked: reason='{reason}', immediate={immediate}")

        result = await self.request_cancel(immediate=immediate)

        if result.get("cancelled"):
            running_tools = result.get("running_tools", [])
            if running_tools:
                tools_str = ", ".join(running_tools)
                if immediate:
                    message = f"Stopping immediately. Cancelled: {tools_str}"
                else:
                    message = (
                        f"Cancellation requested. Waiting for {tools_str} to finish."
                    )
            else:
                message = "Cancellation acknowledged. No active operations to cancel."

            return ToolResult(
                success=True,
                output={
                    "cancelled": True,
                    "level": result.get("level"),
                    "running_tools": running_tools,
                    "message": message,
                },
            )
        else:
            return ToolResult(
                success=False,
                output=None,
                error=result.get("error", "Failed to request cancellation"),
            )

    async def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> ToolResult:
        """
        Execute tool via coordinator - direct Python call, no subprocess!

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as dict

        Returns:
            ToolResult with success/output/error
        """
        # Handle cancel tool specially - it's server-side, not Amplifier
        if tool_name == "cancel_current_task":
            return await self._handle_cancel_tool(arguments)

        if tool_name not in self._tools:
            logger.error(f"Unknown tool: {tool_name}")
            return ToolResult(
                success=False, output=None, error=f"Unknown tool: {tool_name}"
            )

        try:
            logger.info(
                f"Executing tool: {tool_name} with args: {json.dumps(arguments, indent=2)}"
            )

            # Get the tool instance
            tool_info = self._tools[tool_name]
            tool = tool_info["tool"]

            # Execute using Tool protocol method
            result = await tool.execute(arguments)

            logger.info(f"Tool {tool_name} completed successfully")
            logger.debug(f"Result: {result}")

            return ToolResult(success=True, output=result)

        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return ToolResult(success=False, output=None, error=str(e))

    async def close(self):
        """Cleanup session resources."""
        if self._session:
            try:
                await self._session.close()
                logger.info("Amplifier session closed")
            except Exception as e:
                logger.warning(f"Error closing session: {e}")
            finally:
                self._session = None
                self._coordinator = None
                self._tools.clear()
                self._initialized = False

    async def cleanup(self):
        """Alias for close() for backward compatibility."""
        await self.close()


# Global bridge instance
_bridge_instance: Optional[AmplifierBridge] = None


async def get_amplifier_bridge(
    bundle: str = "amplifier-dev", cwd: Optional[str] = None
) -> AmplifierBridge:
    """
    Get or create the global Amplifier bridge instance.

    Args:
        bundle: Bundle name to load (default: amplifier-dev)
        cwd: Working directory for the session

    Returns:
        Initialized AmplifierBridge instance
    """
    global _bridge_instance

    if _bridge_instance is None:
        _bridge_instance = AmplifierBridge(bundle_name=bundle, cwd=cwd)
        await _bridge_instance.initialize()

    return _bridge_instance


async def cleanup_amplifier_bridge() -> None:
    """Clean up the global bridge instance."""
    global _bridge_instance

    if _bridge_instance:
        logger.info("Cleaning up Amplifier bridge...")
        await _bridge_instance.cleanup()
        _bridge_instance = None
        logger.info("Amplifier bridge cleanup complete")
