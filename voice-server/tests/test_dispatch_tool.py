"""Tests for the dispatch tool definition — async fire-and-forget delegation."""

from voice_server.tools.dispatch_tool import DISPATCH_TOOL_DEFINITION


class TestDispatchToolStructure:
    """DISPATCH_TOOL_DEFINITION has the correct top-level shape."""

    def test_name_is_dispatch(self):
        assert DISPATCH_TOOL_DEFINITION["name"] == "dispatch"

    def test_type_is_function(self):
        assert DISPATCH_TOOL_DEFINITION["type"] == "function"

    def test_has_description(self):
        assert "description" in DISPATCH_TOOL_DEFINITION
        assert DISPATCH_TOOL_DEFINITION["description"]  # non-empty

    def test_has_parameters(self):
        assert "parameters" in DISPATCH_TOOL_DEFINITION

    def test_parameters_type_is_object(self):
        assert DISPATCH_TOOL_DEFINITION["parameters"]["type"] == "object"


class TestDispatchToolParameters:
    """Parameter schema has the right required and optional fields."""

    def setup_method(self):
        self.params = DISPATCH_TOOL_DEFINITION["parameters"]
        self.properties = self.params["properties"]
        self.required = self.params["required"]

    def test_agent_parameter_exists(self):
        assert "agent" in self.properties

    def test_instruction_parameter_exists(self):
        assert "instruction" in self.properties

    def test_context_depth_parameter_exists(self):
        assert "context_depth" in self.properties

    def test_agent_is_required(self):
        assert "agent" in self.required

    def test_instruction_is_required(self):
        assert "instruction" in self.required

    def test_context_depth_is_optional(self):
        """context_depth must NOT appear in the required list."""
        assert "context_depth" not in self.required

    def test_agent_is_string_type(self):
        assert self.properties["agent"]["type"] == "string"

    def test_instruction_is_string_type(self):
        assert self.properties["instruction"]["type"] == "string"

    def test_context_depth_is_string_type(self):
        assert self.properties["context_depth"]["type"] == "string"


class TestDispatchToolDescription:
    """Description content conveys async / fire-and-forget semantics and links to delegate."""

    def setup_method(self):
        self.description = DISPATCH_TOOL_DEFINITION["description"]

    def test_description_mentions_async_or_fire_and_forget(self):
        desc_lower = self.description.lower()
        assert "async" in desc_lower or "fire-and-forget" in desc_lower

    def test_description_references_delegate_tool(self):
        """Model must know dispatch shares the same agent pool as delegate."""
        assert "delegate" in self.description.lower()

    def test_description_mentions_returns_immediately_or_notified(self):
        """User should understand it returns before the work is done."""
        desc_lower = self.description.lower()
        assert "immediately" in desc_lower or "notified" in desc_lower
