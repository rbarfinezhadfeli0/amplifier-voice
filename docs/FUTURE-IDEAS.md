# Future Ideas

Ideas for future consideration. Not prescriptive -- just inspiration to revisit when the time is right.

---

## MCP Integration

The GA Realtime API supports MCP server configuration, letting the model discover and call external tools dynamically. Instead of maintaining a fixed tool list, Amplifier tools could be exposed via MCP and the voice model would discover capabilities at runtime. This pairs naturally with async function calling -- MCP-discovered tools could use either `delegate` or `dispatch` semantics depending on expected latency.

## Agent Handoffs (Swarm-style)

The OpenAI Agents SDK supports `RealtimeAgent` with handoffs between specialized voice personas. Instead of one voice agent that delegates everything through a single set of instructions, specialized voice personas (coding assistant, research assistant, home automation) could hand off to each other mid-session. Each persona gets its own voice, instructions, and tool configuration, but they share the same session context.

## Output Guardrails

Content checks on model speech output before it reaches the user. The primary use case is preventing accidental leakage of sensitive information -- API keys, file paths, credentials, internal system details -- in spoken responses. The Agents SDK provides `OutputGuardrail` with debounced transcript checking that could be adapted to the voice pipeline.

## Tool Approval Gates

Human-in-the-loop confirmation before destructive operations. The model would pause and ask: "I'm about to push to main -- should I go ahead?" and wait for voice confirmation before proceeding. The Agents SDK supports `tool_approval_required` events that could drive this flow.

## Hosted Prompts

Store and version system instructions separately from code. Reference prompts by ID with template variables at session time. This decouples prompt iteration from deployment cycles and enables A/B testing different prompt versions, per-user prompt customization, and gradual rollouts of instruction changes.

## SIP Telephony

Direct phone number connection to the voice assistant via Twilio or SIP trunk. The sideband architecture carries over unchanged -- the only difference is SIP instead of WebRTC on the client side. This opens up phone-based access to Amplifier without any browser.

## Cross-Mode Context Bridging

Inject text-mode conversation history into voice sessions so the voice model is aware of what happened in prior text interactions. The `os-idea` project does this by injecting the last 20 turns from hot memory via `inject_history()`. A similar approach with Amplifier session context would let users seamlessly switch between text and voice modes without losing thread.
