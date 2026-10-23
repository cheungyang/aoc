"""Control-thread commands: typed in the ownership control thread, where prod
(the NAS) and dev (a laptop) agree on which instance answers for each agent
(core/channel/discord/ownership.py).

    claim.py     [claim <agent>]   the dev instance takes <agent>
    release.py   [release]         every agent back to prod
    status.py    [status]          each instance says who holds what

The thread is a log: ownership replays it through these commands' `match` to
rebuild who holds what, at startup and as messages arrive. Their `run` only
answers, live: a reaction or a status post, through ControlThread (../ports.py),
which the Discord runner provides.
"""
