"""Dispatch tool definition — async fire-and-forget delegation."""

DISPATCH_TOOL_DEFINITION = {
    "type": "function",
    "name": "dispatch",
    "description": (
        "Fire-and-forget async delegation for complex tasks that take time. "
        "Accepts the same agents as the delegate tool. Returns immediately — "
        "the user will be notified when the task completes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Which specialist agent to use. Accepts any agent available in the delegate tool.",
            },
            "instruction": {
                "type": "string",
                "description": "What the agent should do.",
            },
            "context_depth": {
                "type": "string",
                "description": "How much conversation context to include. Options: 'none', 'recent', 'all'. Default: 'recent'.",
            },
        },
        "required": ["agent", "instruction"],
    },
}
