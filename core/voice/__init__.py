"""Voice: talking to the agents in a Discord voice room.

  the room, at the root:
    voice_manager.py   one bot's voice: wires listening to speaking, and runs turns
    turn.py            one turn: everything still unanswered, answered as one request
    hooks.py           how features built on the room join in, without the voice core knowing them
  foundation/          what every part shares: the settings, and the utterance
  engines/             speech-to-text and text-to-speech
  listening/           the user's side: utterances, turn-taking, the voiceprint gate and `!enroll`
  speech/              what the agent says on a voice turn, and how it is played
  room/                which voice channel to join, and the focus: which agent the next turn is for
  briefing/            a feature built on the room: voice briefings of the channel inbox

Layers, lowest first. A module imports from its own layer or the ones below
it, never above (checked by tests/test_layers.py):

  foundation   foundation/: imports nothing else from voice
  engines      engines/
  parts        listening/, speech/, room/: never import each other; they meet
               in VoiceManager, through the Protocols each one declares
  the room     voice_manager, turn, hooks
  features     briefing/: joins as a hook; the voice core never imports it

Rules:
  1. Voice imports no other adapter (core.channel.discord, core.scheduler,
     graphs, tools, scripts). What it needs from Discord's text side, the
     Discord runner, which hosts the voice room, passes in.
  2. Inside voice, imports point down the layers above.
  3. Of the adapters, only the Discord runner and scripts import voice; the
     runtime and the packages below it never do.
  4. A package __init__.py is a docstring: no imports, no side effects.
  5. An import inside a function is for an optional heavy dependency
     (faster_whisper, onnxruntime, the Google TTS client, edge-tts,
     websockets), never to dodge a cycle.

Importing this package does nothing: the voice room loads libopus and patches
the Discord libraries itself, just before it connects (room/patches.py).
"""
