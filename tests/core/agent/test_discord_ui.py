import unittest
from unittest.mock import AsyncMock, MagicMock
import discord
from core.agent.discord_ui import PollButtonView, PollSelectView


class TestDiscordUI(unittest.IsolatedAsyncioTestCase):
    def test_poll_button_view_truncates_long_labels(self):
        long_text = "A" * 120
        poll_data = {
            "question": "Choose an option",
            "allow_multiple": False,
            "options": [
                {"text": "Short label", "emoji": "👍", "response": "Option 1"},
                {"text": long_text, "emoji": None, "response": "Option 2"},
                {"text": "", "emoji": None, "response": "Option 3"},
            ]
        }
        mock_channel = AsyncMock()
        view = PollButtonView(poll_data, mock_channel)

        self.assertEqual(len(view.children), 3)
        # First button remains intact
        self.assertEqual(view.children[0].label, "Short label")
        self.assertEqual(str(view.children[0].emoji), "👍")

        # Second button is truncated to 80 chars
        self.assertEqual(len(view.children[1].label), 80)
        self.assertTrue(view.children[1].label.endswith("..."))
        self.assertEqual(view.children[1].label, "A" * 77 + "...")

        # Third button with empty label and no emoji gets fallback label
        self.assertEqual(view.children[2].label, "Select")

    def test_poll_button_view_max_options(self):
        poll_data = {
            "question": "Lots of options",
            "allow_multiple": False,
            "options": [{"text": f"Opt {i}", "emoji": None, "response": f"R{i}"} for i in range(35)]
        }
        mock_channel = AsyncMock()
        view = PollButtonView(poll_data, mock_channel)

        # Discord caps at 25 buttons
        self.assertEqual(len(view.children), 25)

    async def test_poll_button_callback(self):
        poll_data = {
            "question": "Choose an option",
            "allow_multiple": False,
            "options": [
                {"text": "Approve", "emoji": None, "response": "Approved plan"},
            ]
        }
        mock_channel = AsyncMock()
        view = PollButtonView(poll_data, mock_channel)

        mock_interaction = MagicMock()
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.response.defer = AsyncMock()

        button = view.children[0]
        await button.callback(mock_interaction)

        mock_channel.send.assert_called_once_with("<@12345>: Approved plan")
        mock_interaction.response.defer.assert_called_once()

    def test_poll_select_view_truncates_long_labels(self):
        long_text = "B" * 150
        poll_data = {
            "question": "Select multiple",
            "allow_multiple": True,
            "options": [
                {"text": "Short label", "emoji": "🎉", "response": "Opt 1"},
                {"text": long_text, "emoji": None, "response": "Opt 2"},
                {"text": "", "emoji": None, "response": "Opt 3"},
            ]
        }
        mock_channel = AsyncMock()
        view = PollSelectView(poll_data, mock_channel)

        self.assertEqual(len(view.children), 1)
        select = view.children[0]
        self.assertEqual(len(select.options), 3)

        # Option 1
        self.assertEqual(select.options[0].label, "Short label")
        self.assertEqual(str(select.options[0].emoji), "🎉")

        # Option 2 is truncated to 100 chars
        self.assertEqual(len(select.options[1].label), 100)
        self.assertTrue(select.options[1].label.endswith("..."))
        self.assertEqual(select.options[1].label, "B" * 97 + "...")

        # Option 3 empty label fallback
        self.assertEqual(select.options[2].label, "Option 3")

    def test_poll_select_view_max_options(self):
        poll_data = {
            "question": "Lots of options",
            "allow_multiple": True,
            "options": [{"text": f"Opt {i}", "emoji": None, "response": f"R{i}"} for i in range(30)]
        }
        mock_channel = AsyncMock()
        view = PollSelectView(poll_data, mock_channel)

        select = view.children[0]
        self.assertEqual(len(select.options), 25)

    async def test_poll_select_callback(self):
        poll_data = {
            "question": "Select multiple",
            "allow_multiple": True,
            "options": [
                {"text": "Opt 0", "emoji": None, "response": "Response 0"},
                {"text": "Opt 1", "emoji": None, "response": "Response 1"},
            ]
        }
        mock_channel = AsyncMock()
        view = PollSelectView(poll_data, mock_channel)

        mock_interaction = MagicMock()
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.data = {"values": ["0", "1"]}
        mock_interaction.response.defer = AsyncMock()

        select = view.children[0]
        await select.callback(mock_interaction)

        mock_channel.send.assert_called_once_with("<@12345>: Response 0, Response 1")
        mock_interaction.response.defer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
