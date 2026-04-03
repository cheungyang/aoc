import os
import pickle
from typing import Optional, List, Iterator, Sequence, Any
from collections import defaultdict
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    PendingWrite
)
import json
import time
import shutil

SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sessions"))
CHECKPOINTS_DIR = os.path.join(SESSIONS_DIR, "checkpoints")

class FlatFileCheckpointer(BaseCheckpointSaver):
    def __init__(self, directory: str = CHECKPOINTS_DIR):
        super().__init__()
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _get_file_path(self, thread_id: str) -> str:
        safe_id = thread_id.replace(":", "_").replace("/", "_")
        return os.path.join(self.directory, f"{safe_id}.pkl")

    def _load_data(self, thread_id: str) -> dict:
        file_path = self._get_file_path(thread_id)
        if os.path.exists(file_path):
            try:
                with open(file_path, "rb") as f:
                    return pickle.load(f)
            except Exception as e:
                return {"checkpoints": {}, "writes": defaultdict(list)}
        return {"checkpoints": {}, "writes": defaultdict(list)}

    def _save_data(self, thread_id: str, data: dict):
        file_path = self._get_file_path(thread_id)
        with open(file_path, "wb") as f:
            pickle.dump(data, f)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            return None
        
        data = self._load_data(thread_id)
        checkpoints = data.get("checkpoints", {})
        
        if not checkpoints:
            return None
            
        checkpoint_id = config["configurable"].get("checkpoint_id")
        
        if checkpoint_id:
            if checkpoint_id in checkpoints:
                entry = checkpoints[checkpoint_id]
                return CheckpointTuple(
                    config=entry["config"],
                    checkpoint=entry["checkpoint"],
                    metadata=entry["metadata"],
                    parent_config=entry.get("parent_config"),
                    pending_writes=entry.get("pending_writes")
                )
            return None
        
        sorted_entries = sorted(
            checkpoints.values(),
            key=lambda x: (x["metadata"].get("step", -1), x["checkpoint"].get("id", ""))
        )
        
        if not sorted_entries:
            return None
            
        latest_entry = sorted_entries[-1]
        
        return CheckpointTuple(
            config=latest_entry["config"],
            checkpoint=latest_entry["checkpoint"],
            metadata=latest_entry["metadata"],
            parent_config=latest_entry.get("parent_config"),
            pending_writes=latest_entry.get("pending_writes")
        )

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            raise ValueError("thread_id is required in config")
            
        data = self._load_data(thread_id)
        
        checkpoint_id = checkpoint["id"]
        return_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id
            }
        }
        
        data["checkpoints"][checkpoint_id] = {
            "config": return_config,
            "checkpoint": checkpoint,
            "metadata": metadata,
            "new_versions": new_versions
        }
        
        self._save_data(thread_id, data)
        return return_config

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            return
            
        data = self._load_data(thread_id)
        checkpoint_id = config["configurable"].get("checkpoint_id")
        
        if "writes" not in data:
            data["writes"] = defaultdict(list)
            
        data["writes"][task_id].append({
            "writes": writes,
            "task_path": task_path,
            "checkpoint_id": checkpoint_id
        })
        
        self._save_data(thread_id, data)

    def list(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        all_checkpoints = []
        if not os.path.exists(self.directory):
            return iter(all_checkpoints)
            
        for thread_file in os.listdir(self.directory):
            if not thread_file.endswith(".pkl"):
                continue
            file_path = os.path.join(self.directory, thread_file)
            try:
                with open(file_path, "rb") as f:
                    data = pickle.load(f)
                    for cp_id, entry in data.get("checkpoints", {}).items():
                        if before and before["configurable"].get("checkpoint_id") == cp_id:
                            continue
                            
                        if filter:
                            match = True
                            for k, v in filter.items():
                                if entry["metadata"].get(k) != v:
                                    match = False
                                    break
                            if not match:
                                continue
                                
                        all_checkpoints.append(CheckpointTuple(
                            config=entry["config"],
                            checkpoint=entry["checkpoint"],
                            metadata=entry["metadata"],
                            parent_config=entry.get("parent_config"),
                            pending_writes=entry.get("pending_writes")
                        ))
            except Exception:
                pass

        all_checkpoints.sort(key=lambda x: (x.metadata.get("step", -1), x.checkpoint.get("id", "")), reverse=True)
        
        if limit:
            all_checkpoints = all_checkpoints[:limit]
            
        return iter(all_checkpoints)

    def delete_thread(self, thread_id: str) -> None:
        file_path = self._get_file_path(thread_id)
        if os.path.exists(file_path):
            os.remove(file_path)

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return self.get_tuple(config)

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        return self.put_writes(config, writes, task_id, task_path)

    async def alist(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item
