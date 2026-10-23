"""Commands: every command a user gives the system itself rather than an agent,
typed or spoken. Each is one file here; nothing outside this folder defines a
command.

    base.py       Command, the base class; its kinds by how a command is given
                  (TypedCommand, SpokenCommand, ControlCommand); their contexts
    ports.py      what commands act on, provided by whoever runs them:
                  VoiceControls (the voice room), SpokenTurn, ControlThread
    registry.py   CommandRegistry; TYPED, SPOKEN and CONTROL, one per way in
    typed/        `!name`, typed to the Discord bot. In a channel the bot
                  answers: !new, !newall, !compact, !restart, !help. For the
                  voice room: !join, !leave, !inbox, !enroll, !voiceprint, !gate
    spoken/       said in the voice room: "stop", "wait", "next agent",
                  "where am I", "switch me to Daisy"
    control/      typed in the ownership control thread: [claim <agent>],
                  [release], [status]

Who runs them: the Discord runner asks TYPED about every message starting with
"!", and CONTROL about the control thread (core/channel/discord/runner.py); the
voice turn asks SPOKEN about every finished utterance (core/voice/turn.py).
`!help` lists them all.

The pattern, as for prompts and storage: one file per command. A subclass of a
kind sets its name and summary -- and its usage, aliases or phrases -- and
implements run(). A command never imports an adapter: what it acts on comes in
its context, typed by the Protocols in ports.py.

Not commands, though they look like them: concierge prefixes ("[main] ...",
"kill ...") route a message to an agent (core/runtime/foundation/routing.py).
"""
