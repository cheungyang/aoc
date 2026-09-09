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

if 'mcp' not in sys.modules:
    sys.modules['mcp'] = MagicMock()
    sys.modules['mcp.client'] = MagicMock()
    sys.modules['mcp.client.stdio'] = MagicMock()

if 'langchain_mcp_adapters' not in sys.modules:
    sys.modules['langchain_mcp_adapters'] = MagicMock()
    sys.modules['langchain_mcp_adapters.tools'] = MagicMock()

if 'croniter' not in sys.modules:
    sys.modules['croniter'] = MagicMock()
