"""What every part of the runtime, and the packages below it, share.

  context    who a turn runs as: ExecutionContext and the ambient current one
  sessions   SessionManager, the only way to mint a context; clears and archives sessions
  jobs       JobManager: what's running, and the kill handshake
  events     what a turn streams, and what it leaves for its caller
  routing    who answers an untagged message in a channel (pure policy)

The foundation imports nothing else from the runtime, and it is the only part of
the runtime the packages below it (core.prompts, loaders, storage, ...) may import.
"""
