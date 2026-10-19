import sys
from unittest.mock import MagicMock

try:
    import discord
except ImportError:
    if 'discord' not in sys.modules:
        mock_discord = MagicMock()
        class MockThread:
            def __init__(self, *args, **kwargs):
                self.parent = None
                self.name = ""
                self.id = ""
            def typing(self):
                pass
            def history(self, limit=2):
                pass
            async def send(self, *args, **kwargs):
                pass
        mock_discord.Thread = MockThread
        sys.modules['discord'] = mock_discord
        sys.modules['discord.ext'] = MagicMock()
        sys.modules['discord.ext.commands'] = MagicMock()
        sys.modules['discord.ui'] = MagicMock()

# Mock croniter only when it is not installed: a mock accepts every cron
# expression and returns mock run times, which hides real schedule bugs.
try:
    import croniter
except ImportError:
    if 'croniter' not in sys.modules:
        sys.modules['croniter'] = MagicMock()
