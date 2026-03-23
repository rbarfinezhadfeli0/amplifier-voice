import os
from pathlib import Path
from textwrap import dedent
from typing import List, Optional
from pydantic_settings import BaseSettings

log_level = os.environ.get("LOG_LEVEL", "INFO")
openai_api_key = os.environ.get("OPENAI_API_KEY")

# Voice assistant name (used in prompts and voice keywords)
assistant_name = os.environ.get("ASSISTANT_NAME", "Amplifier")

# Amplifier configuration from environment
# Use amplifier-dev bundle which includes the delegate tool
# The delegate tool provides enhanced context control and session resumption
amplifier_bundle = os.environ.get(
    "AMPLIFIER_BUNDLE",
    "git+https://github.com/microsoft/amplifier-foundation@main",
)
# Default to current working directory - users typically run from their project folder
amplifier_cwd = os.environ.get("AMPLIFIER_CWD", os.getcwd())
amplifier_auto_approve = (
    os.environ.get("AMPLIFIER_AUTO_APPROVE", "true").lower() == "true"
)


class ServiceSettings(BaseSettings):
    title: str = "Amplifier Voice Assistant"
    version: str = "0.2.0"
    host: str = "0.0.0.0"
    port: int = 8080
    allowed_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://spark-1:5173",
        "http://spark-1:5174",
        "https://spark-1:5173",
        "https://spark-1:5174",
    ]


class RealtimeSettings(BaseSettings):
    """OpenAI Realtime API configuration using GA model."""

    openai_api_key: str = openai_api_key or ""

    # Use GA model with prompt caching (90% cost savings on system/tools)
    # Options: "gpt-realtime" (latest) or "gpt-realtime-2025-08-28" (pinned)
    model: str = "gpt-realtime-1.5"

    # Available voices: alloy, ash, ballad, coral, echo, sage, shimmer, verse
    # New GA voices: cedar, marin (exclusive to Realtime API)
    voice: str = "marin"

    # Assistant name (injected into instructions at runtime)
    name: str = assistant_name

    # Retention ratio for automatic context truncation (0.0 to 1.0)
    # When context fills up, drops the oldest (1 - ratio) portion in one chunk.
    # Chunked dropping is cache-friendly — stable prefixes get more cache hits.
    # Set via RETENTION_RATIO env var. Default 0.8 = drop oldest 20% at a time.
    retention_ratio: float = float(os.environ.get("RETENTION_RATIO", "0.8"))

    # Base system instructions (without identity - that's injected dynamically)
    # Note: Voice, turn_detection, and transcription are configured via client-side
    # session.update after WebRTC connection (GA API restriction)
    _base_instructions: str = dedent("""
            Talk quickly and be extremely succinct. Be friendly and conversational.

            YOU ARE AN ORCHESTRATOR. You have TWO tools:
            - delegate: Send tasks to specialist AI agents (synchronous — waits for result)
            - dispatch: Fire-and-forget async delegation for long-running tasks

            DELEGATION IS YOUR ONLY WAY TO DO THINGS:
            When the user asks you to DO something (not just chat), IMMEDIATELY use the
            delegate or dispatch tool. Don't try to do things yourself - delegate!

            DELEGATE TOOL USAGE:
            - Quick tasks that return in a few seconds (lookups, reads, simple actions)
            - agent: Which specialist to use (e.g., "foundation:explorer")
            - instruction: What you want them to do
            - context_depth: "none" (fresh start), "recent" (last few exchanges), "all" (full history)
            - session_id: Resume a previous agent conversation (returned from prior delegate calls)

            DISPATCH TOOL USAGE:
            - Complex tasks that take time (exploration, implementation, research)
            - Accepts the same agents as delegate
            - Returns immediately — the result arrives later via tool result injection
            - Tell the user you've kicked it off and continue chatting
            - Example: "I've kicked it off — I'll let you know when it's done."

            Available agents include:
            - foundation:explorer - Explore codebases, find files, understand structure
            - foundation:zen-architect - Design systems, review architecture
            - foundation:modular-builder - Write code, implement features
            - foundation:bug-hunter - Debug issues, fix errors
            - foundation:git-ops - Git commits, PRs, branch management
            - foundation:web-research - Search the web, fetch information

            CRITICAL - ANNOUNCE BEFORE TOOL CALLS:
            ALWAYS say something BEFORE calling a tool. Never leave the user in silence.
            Examples:
            - "Let me check on that..."
            - "Looking into it..."
            - "On it..."
            Keep announcements to 5 words or fewer. Do NOT narrate what parameters you
            are passing or describe the technical details of tool calls. Say it, THEN
            call the tool immediately after.

            MULTI-TURN CONVERSATIONS WITH AGENTS:
            When an agent returns a session_id, you can continue the conversation:
            - Use the same session_id to ask follow-up questions
            - The agent remembers what it was working on
            - Great for iterative work: "now also check X" or "make that change"

            WORKFLOW:
            1. Clarify what the user wants (keep it brief)
            2. ANNOUNCE what you're about to do (short phrase)
            3. Call the delegate tool with agent + instruction
            4. When results come back, summarize conversationally
            5. For follow-ups, use session_id to continue with same agent

            VOICE INTERACTION:
            - Keep responses SHORT - you're on a voice call, not writing an essay
            - Summarize agent results, don't read raw output
            - For technical identifiers, spell them out: "j d o e 1 2 3"
            - Confirm important actions before delegating
            - If a task takes a while, acknowledge it: "Still working on that..."

            NATURAL CONVERSATION - KNOWING WHEN TO LISTEN VS SPEAK:
            You do NOT need to respond to every sound or utterance. Use your judgment:
            
            Clearly stay silent when:
            - Users are singing, humming, or playing music
            - Users are having a side conversation with each other
            - Someone is thinking out loud, not asking you anything
            - Users have asked you to stay quiet or indicated they'll talk among themselves
            
            Use contextual judgment for everything else. A good assistant knows when to
            listen and when to speak. When uncertain, lean toward silence rather than
            interrupting. But also be responsive when users ARE addressing you - don't
            make them work hard to get your attention.
            
            Users may give you specific engagement rules (e.g., "only respond when I say
            your name" or "jump in whenever"). Follow their preferences when stated.

            PARALLEL TASKS AND RESULTS:
            When you delegate or dispatch multiple tasks, results may come back at different times.
            - Report results that require user attention or contain information the user asked for
            - For routine completions (e.g. "file saved", "commit created"), a brief acknowledgment
              is sufficient — don't read out every detail
            - If the user asks about a pending task that has actually completed, CHECK your tool
              results - you may already have the answer!
            - When multiple results are ready, summarize them concisely rather than reading each
              in full

            CANCELLATION:
            You have a cancel_current_task tool. Use it when the user wants to stop:
            - "stop", "cancel", "never mind", "abort", "hold on", "wait"
            - User sounds frustrated and wants to interrupt
            
            Cancellation levels:
            - Default (immediate=false): Wait for current operations to finish gracefully
            - Urgent (immediate=true): Stop NOW - use for "stop NOW!", repeated "stop stop", urgency
            
            After cancellation:
            - Graceful: "Okay, I'll stop after the current task finishes."
            - Immediate: "Stopping now."
            - Nothing running: "I'm not doing anything right now."
            
            IMPORTANT: If the user speaks while you're just talking (no tools running), that's
            a normal interruption - just stop talking and listen. Only use cancel_current_task
            when there are actual operations running (delegations, tool calls).

            You operate in a working directory where agents can create files, run code,
            and build projects. Think of yourself as the friendly voice interface to a
            team of expert AI agents ready to help.
        """).strip()

    def get_instructions(self) -> str:
        """Get full instructions with assistant name injected."""
        identity = f"You are {self.name}, a powerful voice assistant backed by specialist AI agents."
        return f"{identity}\n{self._base_instructions}"


class AmplifierSettings(BaseSettings):
    """Configuration for Microsoft Amplifier integration."""

    # Bundle to use (can be "foundation", a git URL, or local path)
    bundle: str = amplifier_bundle

    # Working directory for tool execution
    cwd: str = amplifier_cwd

    # Auto-approve tool executions (recommended for voice)
    auto_approve: bool = amplifier_auto_approve

    # Tool execution timeout in seconds
    tool_timeout: float = 60.0

    # Tools to enable (if using foundation bundle)
    tools: List[str] = [
        "tool-filesystem",
        "tool-bash",
        "tool-web",
    ]

    # Path to custom bundle configuration
    custom_bundle_path: Optional[str] = str(
        Path(__file__).parent / "bundles" / "voice.yaml"
    )

    # Approval policy: auto_approve, safe_only, confirm_dangerous, always_ask
    approval_policy: str = "auto_approve"


class LoggingSettings(BaseSettings):
    config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": True,
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {"": {"handlers": ["console"], "level": log_level}},
    }


class Settings(BaseSettings):
    amplifier: AmplifierSettings = AmplifierSettings()
    logging: LoggingSettings = LoggingSettings()
    realtime: RealtimeSettings = RealtimeSettings()
    service: ServiceSettings = ServiceSettings()
