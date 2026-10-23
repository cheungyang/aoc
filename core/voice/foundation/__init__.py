"""What every part of voice shares.

  settings.py    voice settings shared by every agent
  utterance.py   one stretch of the user's speech (Utterance), and what it said
                 (Transcript): listening makes them, the engines transcribe
                 them, the room answers them

The foundation imports nothing else from voice, and every part may import it.
"""
