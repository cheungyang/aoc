"""The voice room: which voice channel, and joining and leaving it
(connection.py, after the fixes to the Discord libraries in patches.py), and
the focus -- which agent and text channel the next turn belongs to (focus.py) --
moved by voice with "switch me to Daisy" (room_switch.py, matching names
through aliases.py), or after "Do you mean fitness coach Frankie?" when the
name was not exact (near_miss.py). The phrases themselves are spoken commands
(core/runtime/commands/spoken/).
"""
