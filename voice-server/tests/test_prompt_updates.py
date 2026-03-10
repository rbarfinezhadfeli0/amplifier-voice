"""Tests for prompt block updates — softer tool announcements, smarter result reporting."""

from voice_server.config import RealtimeSettings


class TestToolAnnouncementBlock:
    """Verify the tool announcement block was softened."""

    def setup_method(self):
        self.instructions = RealtimeSettings().get_instructions()

    def test_short_word_limit_present(self):
        """New guidance says '5 words or fewer' instead of 'under 10 words'."""
        assert "5 words or fewer" in self.instructions

    def test_old_verbose_examples_removed(self):
        """Old agent-specific examples like 'Firing up the web researcher' should be gone."""
        assert "Firing up the web researcher" not in self.instructions
        assert "Let me get explorer on that" not in self.instructions
        assert "I'll have the architect look at this" not in self.instructions
        assert "Let me delegate this to the builder" not in self.instructions

    def test_new_short_examples_present(self):
        """New examples should be generic and short."""
        assert "Let me check on that" in self.instructions
        assert "Looking into it" in self.instructions
        assert "On it" in self.instructions

    def test_no_narrate_guidance_present(self):
        """New guidance about not narrating tool call details should be present."""
        assert "Do NOT narrate what parameters you" in self.instructions

    def test_old_under_10_words_removed(self):
        """Old 'under 10 words' guidance should be gone."""
        assert "under 10 words" not in self.instructions


class TestParallelResultsBlock:
    """Verify the parallel results block was refined."""

    def setup_method(self):
        self.instructions = RealtimeSettings().get_instructions()

    def test_routine_completions_present(self):
        """New guidance about routine completions should be present."""
        assert "routine completions" in self.instructions

    def test_summarize_concisely_present(self):
        """New guidance about summarizing concisely should be present."""
        assert "summarize them concisely" in self.instructions

    def test_old_always_report_removed(self):
        """Old 'ALWAYS report results as soon as they arrive' should be gone."""
        assert "ALWAYS report results as soon as they arrive" not in self.instructions

    def test_old_never_leave_unreported_removed(self):
        """Old 'NEVER leave completed results unreported' should be gone."""
        assert "NEVER leave completed results unreported" not in self.instructions

    def test_old_oh_and_other_task_removed(self):
        """Old 'Oh, and the other task just finished too!' should be gone."""
        assert "Oh, and the other task just finished too!" not in self.instructions

    def test_header_no_longer_critical(self):
        """The parallel results header should no longer say 'CRITICAL'."""
        assert "CRITICAL - PARALLEL TASKS AND RESULTS" not in self.instructions
        assert "PARALLEL TASKS AND RESULTS" in self.instructions

    def test_dispatch_language_present(self):
        """New block mentions 'dispatch' in addition to 'delegate'."""
        assert "delegate or dispatch multiple tasks" in self.instructions
