import os

DEFAULT_BLURP = "assets/sounds/blurps/default_ack.wav"

class BlurpGenerator:
    """Manages static audio cue files per agent."""
    
    @staticmethod
    def get_blurp_audio(voice_config: dict) -> str | None:
        """
        Returns the path to the static blurp sound file from voice_config,
        or falls back to the default static sound file.
        """
        static_path = voice_config.get("audio_blurp")
        if static_path and os.path.exists(static_path):
            return static_path
            
        if os.path.exists(DEFAULT_BLURP):
            return DEFAULT_BLURP
            
        return None
