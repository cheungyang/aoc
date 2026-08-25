import pytest
import nacl.secret
from unittest.mock import AsyncMock, MagicMock, patch
import discord.opus

import core.voice  # triggers patches

def test_patch_voice_recv_opus_safe_decode():
    import discord.ext.voice_recv.opus as vr_opus
    
    mock_router = MagicMock()
    mock_router.sink.wants_opus.return_value = False
    
    decoder_obj = vr_opus.PacketDecoder(mock_router, ssrc=12345)
    mock_decoder = MagicMock()
    mock_decoder.decode.return_value = b"\x00" * 3840
    decoder_obj._decoder = mock_decoder
    
    mock_packet = MagicMock()
    mock_packet.decrypted_data = b"raw_opus_frame"
    mock_packet.ssrc = 12345
    
    pkt, pcm = decoder_obj._decode_packet(mock_packet)
    assert pkt == mock_packet
    assert pcm == b"\x00" * 3840
    mock_decoder.decode.assert_called_with(b"raw_opus_frame", fec=False)

def test_patch_voice_recv_opus_dave_decrypt():
    import discord.ext.voice_recv.opus as vr_opus
    
    mock_router = MagicMock()
    mock_router.sink.wants_opus.return_value = False
    
    mock_vc = MagicMock()
    mock_conn = MagicMock()
    mock_dave = MagicMock()
    mock_dave.decrypt.return_value = b"decrypted_opus"
    mock_conn.dave_session = mock_dave
    mock_conn.dave_protocol_version = 1
    mock_vc._connection = mock_conn
    mock_vc._get_id_from_ssrc.return_value = 999888777
    
    mock_router.reader.voice_client = mock_vc
    
    decoder_obj = vr_opus.PacketDecoder(mock_router, ssrc=12345)
    mock_decoder = MagicMock()
    mock_decoder.decode.return_value = b"\x00" * 3840
    decoder_obj._decoder = mock_decoder
    
    mock_packet = MagicMock()
    mock_packet.decrypted_data = b"dave_encrypted_payload"
    mock_packet.ssrc = 12345
    
    pkt, pcm = decoder_obj._decode_packet(mock_packet)
    assert pkt == mock_packet
    mock_dave.decrypt.assert_called_once()
    mock_decoder.decode.assert_called_with(b"decrypted_opus", fec=False)

def test_patch_voice_recv_opus_error_plc_fallback():
    import discord.ext.voice_recv.opus as vr_opus
    
    mock_router = MagicMock()
    mock_router.sink.wants_opus.return_value = False
    
    decoder_obj = vr_opus.PacketDecoder(mock_router, ssrc=12345)
    mock_decoder = MagicMock()
    # First call raises OpusError(-3), second call (PLC) returns silence
    mock_decoder.decode.side_effect = [
        discord.opus.OpusError(-3),
        b"\x00" * 3840
    ]
    decoder_obj._decoder = mock_decoder
    
    mock_packet = MagicMock()
    mock_packet.decrypted_data = b"corrupted_bytes"
    mock_packet.ssrc = 12345
    
    pkt, pcm = decoder_obj._decode_packet(mock_packet)
    assert pkt == mock_packet
    assert pcm == b"\x00" * 3840
    mock_decoder.decode.assert_called_with(None, fec=False)

def test_patch_voice_recv_decryptor_aead():
    import discord.ext.voice_recv.reader as vr_reader
    
    decryptor = vr_reader.PacketDecryptor("aead_xchacha20_poly1305_rtpsize", b"0" * 32)
    mock_box = MagicMock(spec=nacl.secret.Aead)
    mock_box.decrypt.return_value = b"\x00\x00\x00\x00pure_audio_data"
    decryptor.box = mock_box
    
    mock_packet = MagicMock()
    mock_packet.nonce = b"1234"
    mock_packet.data = b"encrypted_payload"
    mock_packet.header = b"rtp_header"
    mock_packet.extended = True
    mock_packet.update_ext_headers.return_value = 4  # 4 byte extension header offset
    
    result = decryptor._decrypt_rtp_aead_xchacha20_poly1305_rtpsize(mock_packet)
    assert result == b"pure_audio_data"

@pytest.mark.asyncio
async def test_patch_discord_voice_gateway_session_description():
    import discord.gateway as gw
    
    ws = MagicMock()
    ws.SESSION_DESCRIPTION = 4
    ws.load_secret_key = AsyncMock()
    ws.initial_connection = AsyncMock()
    ws._hook = AsyncMock()
    ws._connection = MagicMock()
    
    # Test message with missing dave_protocol_version
    msg = {"op": 4, "d": {"mode": "aead_xchacha20_poly1305_rtpsize", "secret_key": [0]*32}}
    
    await gw.DiscordVoiceWebSocket.received_message(ws, msg)
    assert msg["d"]["dave_protocol_version"] == 0

def test_load_libopus():
    import core.voice
    from core.voice import _load_libopus
    import discord.opus
    _load_libopus()
    # Opus should be loaded if libopus is available on the system
    assert discord.opus.is_loaded() is True
