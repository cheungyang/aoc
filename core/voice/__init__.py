def _patch_discord_voice_gateway():
    """
    Patches discord.gateway.DiscordVoiceWebSocket.received_message to safely provide
    default dave_protocol_version=0 when connecting to voice channels if not provided.
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

def _patch_voice_recv_opus():
    """
    Patches discord-ext-voice-recv PacketDecoder._decode_packet to support
    Discord DAVE (MLS E2EE) audio stream decryption and prevent OpusError crashes
    from lossy/corrupted UDP frames.
    """
    try:
        import discord.ext.voice_recv.opus as vr_opus
        import discord.opus
        import logging

        log = logging.getLogger("core.voice")

        def safe_decode_packet(self, packet):
            if self._decoder is None:
                return packet, b""

            if packet:
                data = packet.decrypted_data
                
                # Check for DAVE E2EE
                try:
                    vc = getattr(getattr(self, "router", None), "reader", None)
                    vc = getattr(vc, "voice_client", None) if vc else None
                    if not vc and hasattr(self, "sink"):
                        vc = getattr(self.sink, "voice_client", None)
                    
                    if vc and hasattr(vc, "_connection"):
                        conn = vc._connection
                        dave_session = getattr(conn, "dave_session", None)
                        if dave_session and getattr(conn, "dave_protocol_version", 0) > 0:
                            # 1. Try cached member / user_id
                            user_id = getattr(self, "_cached_id", None)
                            if not user_id and hasattr(vc, "_get_id_from_ssrc"):
                                ssrc = getattr(packet, "ssrc", 0) or getattr(self, "ssrc", 0)
                                user_id = vc._get_id_from_ssrc(ssrc)
                            
                            # 2. Fallback: single non-bot speaker in channel
                            if not user_id and dave_session.ready:
                                bot_id = str(getattr(vc.user, "id", "")) if getattr(vc, "user", None) else ""
                                other_users = [int(u) for u in dave_session.get_user_ids() if str(u) != bot_id]
                                if len(other_users) == 1:
                                    user_id = other_users[0]
                                    if hasattr(self, "set_user_id"):
                                        self.set_user_id(user_id)

                            if user_id:
                                import davey
                                try:
                                    data = dave_session.decrypt(int(user_id), davey.MediaType.audio, data)
                                except Exception as e:
                                    log.debug(f"[Voice] DAVE decrypt error for user {user_id}: {e}")
                except Exception as e:
                    log.debug(f"[Voice] Error in DAVE resolution: {e}")

                try:
                    pcm = self._decoder.decode(data, fec=False)
                    return packet, pcm
                except discord.opus.OpusError as e:
                    # Packet corrupted or transitional frame -> attempt Packet Loss Concealment (PLC)
                    try:
                        pcm = self._decoder.decode(None, fec=False)
                        return packet, pcm
                    except Exception:
                        return packet, b"\x00" * 3840 # 20ms silence frame (960 stereo samples * 2 bytes)

            # Fake packet handling for forward error correction
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
        elif sys.platform.startswith("win"):
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
_patch_voice_recv_opus()

from .voice_manager import VoiceManager
from .vad_sink import VADSink
from .stt_engine import STTEngine
from .tts_engine import TTSEngine
from .blurp_generator import BlurpGenerator

__all__ = ["VoiceManager", "VADSink", "STTEngine", "TTSEngine", "BlurpGenerator"]
