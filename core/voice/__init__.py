def _patch_voice_recv_opus():
    """
    Patches discord-ext-voice-recv PacketDecoder._decode_packet to prevent
    discord.opus.OpusError: corrupted stream from crashing the PacketRouter loop
    when lossy/silence/corrupted UDP frames arrive over Discord voice.
    """
    try:
        import discord.ext.voice_recv.opus as vr_opus
        import discord.opus

        def safe_decode_packet(self, packet):
            if self._decoder is None:
                return packet, b""

            # 1. Standard packet decode with DAVE E2EE decryption
            if packet:
                data = packet.decrypted_data
                
                try:
                    # Resolve voice_client from PacketDecoder -> router -> reader -> voice_client
                    vc = None
                    if hasattr(self, "router") and hasattr(self.router, "reader"):
                        vc = getattr(self.router.reader, "voice_client", None)
                    if not vc and hasattr(self, "sink") and hasattr(self.sink, "voice_manager"):
                        vc = getattr(self.sink.voice_manager, "voice_client", None)
                        
                    if vc and hasattr(vc, "_connection"):
                        conn = vc._connection
                        dave_session = getattr(conn, "dave_session", None)
                        if dave_session and getattr(conn, "dave_protocol_version", 0) > 0:
                            ssrc = getattr(packet, "ssrc", 0) or getattr(self, "ssrc", 0)
                            user_id = vc._get_id_from_ssrc(ssrc)
                            if user_id:
                                try:
                                    import davey
                                    data = dave_session.decrypt(int(user_id), davey.MediaType.audio, data)
                                except Exception:
                                    pass
                except Exception:
                    pass

                try:
                    pcm = self._decoder.decode(data, fec=False)
                    return packet, pcm
                except discord.opus.OpusError:
                    # Packet corrupted or transitional frame -> attempt Packet Loss Concealment (PLC)
                    try:
                        pcm = self._decoder.decode(None, fec=False)
                        return packet, pcm
                    except Exception:
                        return packet, b"\x00" * 3840 # 20ms silence frame (960 stereo samples * 2 bytes)

            # 2. Fake packet handling for forward error correction
            next_packet = self._buffer.peek_next()
            if next_packet is not None:
                nextdata = next_packet.decrypted_data
                try:
                    pcm = self._decoder.decode(nextdata, fec=True)
                except Exception:
                    pcm = b"\x00" * 3840
            else:
                try:
                    pcm = self._decoder.decode(None, fec=False)
                except Exception:
                    pcm = b"\x00" * 3840

            return packet, pcm

        vr_opus.PacketDecoder._decode_packet = safe_decode_packet
    except Exception as e:
        print(f"[Voice] Warning: could not patch voice_recv opus decoder: {e}")

def _patch_voice_recv_decryptor():
    """
    Patches discord-ext-voice-recv PacketDecryptor._decrypt_rtp_aead_xchacha20_poly1305_rtpsize
    so that it does not slice leading bytes off the decrypted Opus audio frame.
    In AEAD rtpsize mode, extension headers are in the RTP AAD, not the payload.
    """
    try:
        import discord.ext.voice_recv.reader as vr_reader
        import nacl.secret

        def patched_decrypt_aead(self, packet):
            packet.adjust_rtpsize()
            nonce = bytearray(24)
            nonce[:4] = packet.nonce
            voice_data = packet.data

            assert isinstance(self.box, nacl.secret.Aead)
            result = self.box.decrypt(bytes(voice_data), bytes(packet.header), bytes(nonce))

            if packet.extended:
                offset = packet.update_ext_headers(result)
                result = result[offset:]

            return result

        vr_reader.PacketDecryptor._decrypt_rtp_aead_xchacha20_poly1305_rtpsize = patched_decrypt_aead
    except Exception as e:
        print(f"[Voice] Warning: could not patch decryptor: {e}")

def _patch_discord_voice_gateway():
    """
    Patches discord.gateway.DiscordVoiceWebSocket.received_message to safely provide
    default dave_protocol_version=0 when connecting to voice channels.
    """
    try:
        import discord.gateway as gw
        orig_received_message = gw.DiscordVoiceWebSocket.received_message

        async def patched_received_message(self, msg):
            if isinstance(msg, dict) and msg.get("op") == self.SESSION_DESCRIPTION and isinstance(msg.get("d"), dict):
                msg["d"].setdefault("dave_protocol_version", 0)
            return await orig_received_message(self, msg)

        gw.DiscordVoiceWebSocket.received_message = patched_received_message
    except Exception as e:
        print(f"[Voice] Warning: could not patch voice gateway: {e}")

def _load_libopus():
    """
    Ensures libopus shared library is loaded on macOS / Linux / Windows.
    On macOS / Apple Silicon, ctypes.util.find_library often fails to find Homebrew / GStreamer opus.
    """
    try:
        import discord.opus
        if discord.opus.is_loaded():
            return

        import os
        import sys
        import ctypes.util

        # 1. Try standard default
        if discord.opus._load_default():
            return

        # 2. Check known paths by platform
        candidates = []
        if sys.platform == "darwin":
            candidates = [
                "/Library/Frameworks/GStreamer.framework/Versions/1.0/lib/libopus.dylib",
                "/opt/homebrew/lib/libopus.dylib",
                "/opt/homebrew/lib/libopus.0.dylib",
                "/usr/local/lib/libopus.dylib",
                "/usr/local/lib/libopus.0.dylib",
                "/opt/homebrew/opt/opus/lib/libopus.dylib",
                "/usr/local/opt/opus/lib/libopus.dylib",
            ]
        elif sys.platform.startswith("linux"):
            candidates = [
                "libopus.so.0",
                "libopus.so",
                "/usr/lib/x86_64-linux-gnu/libopus.so.0",
                "/usr/lib/aarch64-linux-gnu/libopus.so.0",
                "/usr/local/lib/libopus.so",
            ]
        elif sys.platform == "win32":
            candidates = [
                "opus.dll",
                "libopus-0.dll",
            ]

        for path in candidates:
            if os.path.exists(path) or not os.path.isabs(path):
                try:
                    discord.opus.load_opus(path)
                    if discord.opus.is_loaded():
                        return
                except Exception:
                    pass

        # Try find_library as fallback
        lib = ctypes.util.find_library("opus")
        if lib:
            try:
                discord.opus.load_opus(lib)
            except Exception:
                pass
    except Exception as e:
        print(f"[Voice] Warning: Error attempting to load libopus: {e}")

_load_libopus()
_patch_discord_voice_gateway()
_patch_voice_recv_decryptor()
_patch_voice_recv_opus()

from .voice_manager import VoiceManager
from .vad_sink import VADSink
from .stt_engine import STTEngine
from .tts_engine import TTSEngine
from .blurp_generator import BlurpGenerator

__all__ = ["VoiceManager", "VADSink", "STTEngine", "TTSEngine", "BlurpGenerator"]
