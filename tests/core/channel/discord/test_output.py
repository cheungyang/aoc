"""A Discord channel as a turn's output (core/channel/discord/output.py): the message that started the turn."""
from types import SimpleNamespace
from unittest.mock import patch

from core.channel.discord.output import DiscordOutput


def test_only_a_message_id_gives_the_turn_a_source_message():
    receipts = SimpleNamespace(id=111)
    assert DiscordOutput(receipts, message_id=222).source_message == "111/222"
    assert DiscordOutput(receipts).source_message is None


def test_another_channel_keeps_the_message_that_started_the_turn():
    """A turn delegated to another channel's agent still started from your message there."""
    kitchen = SimpleNamespace(id=333)
    with patch("core.channel.discord.loader.BotsLoader.find_channel", return_value=kitchen):
        other = DiscordOutput(SimpleNamespace(id=111), message_id=222).to("meal-planning")
    assert other.channel is kitchen and other.source_message == "111/222"
