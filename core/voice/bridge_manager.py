import os
import re
from typing import Dict, Any, Optional

class BridgeManager:
    """
    Manages dual-track latency masking by selecting immediate, semantically neutral
    floor-holding spoken bridge phrases when tool execution starts, resolving pre-cached
    audio files when available, and falling back to dynamic TTS synthesis.
    """

    # Intent-based bridge phrase templates
    BRIDGE_TEMPLATES = {
        "agent_call": "Checking in with {agent_name} now...",
        "graph_call": "Running the {graph_name} workflow now...",
        "web_search": "Searching online records for you...",
        "browser": "Navigating the web for relevant details...",
        "vector_search": "Checking our knowledge base on that...",
        "filesystem": "Accessing files and notes...",
        "task_query": "Looking up your current tasks...",
        "project_query": "Retrieving your active project records...",
        "zillow_query": "Scanning real estate listings...",
        "seats_aero": "Searching flight reward availability...",
        "bash": "Running system operations now...",
        "default": "Looking into that for you..."
    }

    def __init__(self, cache_dir: str = "assets/sounds/bridge"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    def _resolve_agent_name(self, agent_id: str) -> str:
        """Dynamically resolves the agent's display name from AgentsLoader."""
        if not agent_id:
            return "Specialist"
        try:
            from core.loaders.agents_loader import AgentsLoader
            loader = AgentsLoader()
            agent = loader.get_agent(agent_id)
            if agent:
                name = agent.config.get("name") if hasattr(agent, "config") else None
                if name:
                    return name
        except Exception:
            pass
        return agent_id.replace("-", " ").replace("_", " ").title()

    def _resolve_graph_name(self, graph_name: str) -> str:
        """Dynamically resolves the graph's display name from GraphsLoader."""
        if not graph_name:
            return "Workflow"
        try:
            from core.loaders.graphs_loader import GraphsLoader
            loader = GraphsLoader()
            graph = loader.get_graph(graph_name)
            if graph and hasattr(graph, "config") and graph.config.get("name"):
                return graph.config["name"].title()
        except Exception:
            pass
        return graph_name.replace("-", " ").replace("_", " ").title()

    def get_bridge_phrase(self, agent_id: str, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        Determines the appropriate contextual bridge phrase for a starting tool execution.
        """
        if tool_name == "agent_call":
            target_agent_id = tool_args.get("agent_id", "")
            agent_name = self._resolve_agent_name(target_agent_id)
            return self.BRIDGE_TEMPLATES["agent_call"].format(agent_name=agent_name)

        if tool_name == "graph_call":
            target_graph_id = tool_args.get("graph_name", "")
            graph_name = self._resolve_graph_name(target_graph_id)
            return self.BRIDGE_TEMPLATES["graph_call"].format(graph_name=graph_name)

        if tool_name in self.BRIDGE_TEMPLATES:
            return self.BRIDGE_TEMPLATES[tool_name]

        return self.BRIDGE_TEMPLATES["default"]

    def _get_cache_key(self, agent_id: str, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Computes a deterministic cache filename for static audio pre-caching."""
        if tool_name == "agent_call":
            target = re.sub(r"[^a-zA-Z0-9_-]", "_", str(tool_args.get("agent_id", "specialist")))
            return f"{agent_id}_agent_{target}.mp3"
        if tool_name == "graph_call":
            target = re.sub(r"[^a-zA-Z0-9_-]", "_", str(tool_args.get("graph_name", "workflow")))
            return f"{agent_id}_graph_{target}.mp3"
        return f"{agent_id}_tool_{tool_name}.mp3"

    async def get_or_create_bridge_audio(
        self,
        agent_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        tts_engine,
        voice: Optional[str] = None,
        speed: float = 1.0
    ) -> Optional[str]:
        """
        Returns local audio filepath for the bridge phrase.
        Checks pre-cached assets first; if absent, synthesizes via TTS and saves to cache.
        """
        cache_key = self._get_cache_key(agent_id, tool_name, tool_args)
        agent_cache_dir = os.path.join(self.cache_dir, agent_id)
        os.makedirs(agent_cache_dir, exist_ok=True)
        cached_path = os.path.join(agent_cache_dir, cache_key)

        if os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
            return cached_path

        phrase = self.get_bridge_phrase(agent_id, tool_name, tool_args)
        if not phrase:
            return None

        # Synthesize via TTS engine and persist to cache
        try:
            temp_file = await tts_engine.synthesize_to_file(phrase, voice=voice, speed=speed)
            if temp_file and os.path.exists(temp_file):
                import shutil
                shutil.copyfile(temp_file, cached_path)
                return cached_path
        except Exception as e:
            print(f"[BridgeManager] Error generating bridge audio: {e}")

        return None
