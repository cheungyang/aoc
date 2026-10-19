#!/usr/bin/env python3
"""Expose the desktop's Agent Chrome DevTools port to the NAS, and only the NAS.

Chrome binds --remote-debugging-port to 127.0.0.1 and has no authentication:
whoever reaches the port controls the browser and every site logged in there.
This forwarder listens on the LAN and relays to 127.0.0.1:<chrome port>, but
drops any connection whose source address is not on the allow-list.

Standard library only, so it runs on the stock macOS python3.

    python3 cdp_forwarder.py --allow 192.168.1.10
    python3 cdp_forwarder.py --allow 192.168.1.10 --listen-port 9223 --chrome-port 9222
"""

import argparse
import asyncio
import ipaddress
import logging
import sys

log = logging.getLogger("cdp_forwarder")


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def make_handler(allowed, chrome_host: str, chrome_port: int):
    async def handle(client_reader, client_writer):
        peer = client_writer.get_extra_info("peername") or ("?", 0)
        try:
            source = ipaddress.ip_address(peer[0])
        except ValueError:
            source = None
        if source is None or not any(source in net for net in allowed):
            log.warning("refused connection from %s", peer[0])
            client_writer.close()
            return
        try:
            chrome_reader, chrome_writer = await asyncio.open_connection(chrome_host, chrome_port)
        except OSError as exc:
            log.error("Agent Chrome is not listening on %s:%s (%s)", chrome_host, chrome_port, exc)
            client_writer.close()
            return
        log.info("relaying %s -> %s:%s", peer[0], chrome_host, chrome_port)
        await asyncio.gather(
            _pipe(client_reader, chrome_writer),
            _pipe(chrome_reader, client_writer),
        )

    return handle


async def serve(args) -> None:
    allowed = [ipaddress.ip_network(a, strict=False) for a in args.allow]
    server = await asyncio.start_server(
        make_handler(allowed, "127.0.0.1", args.chrome_port), args.listen_host, args.listen_port
    )
    log.info("listening on %s:%s, allowing %s", args.listen_host, args.listen_port,
             ", ".join(str(n) for n in allowed))
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow", action="append", required=True,
                        help="Source IP or CIDR allowed to connect (the NAS). Repeatable.")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=9223)
    parser.add_argument("--chrome-port", type=int, default=9222)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve(parse_args(argv)))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
