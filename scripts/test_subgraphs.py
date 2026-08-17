import os
import sys
import json
import asyncio
import argparse
from pprint import pprint

# Ensure dev/langgraph root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.util.config import Config
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.subgraph_ideation import create_ideation_subgraph
from graphs.content_creation.subgraph_video import create_video_production_subgraph
from graphs.content_creation.subgraph_copywriting import create_copywriting_subgraph
from graphs.content_creation.graph import create_graph as create_content_creation_graph, ContentCreationState
from langgraph.checkpoint.memory import MemorySaver

def print_separator(title: str):
    print("\n" + "=" * 80)
    print(f"  {title.upper()}")
    print("=" * 80)

def print_state_summary(stage: str, state: dict):
    print(f"\n📊 --- [State at {stage}] ---")
    keys_to_print = [
        "topic", "project_dir", "output_dir", "image_path", "image_prompt",
        "video_plot_path", "video_plot_qc_passed", "video_plot_feedback",
        "raw_video_path", "video_path", "video_persisted", "video_generation_error",
        "remix_actions", "video_qc_passed", "video_qc_feedback", "copy_path",
        "gate1_decision", "gate2_decision", "error_message"
    ]
    for k in keys_to_print:
        if k in state:
            val = state[k]
            if isinstance(val, (list, dict)) and len(str(val)) > 120:
                print(f"  {k}: {json.dumps(val, indent=4)[:200]} ...")
            else:
                print(f"  {k}: {val}")

async def run_ideation_test(topic: str, project_dir: str, output_dir: str):
    print_separator("Testing Ideation Subgraph")
    checkpointer = MemorySaver()
    graph = create_ideation_subgraph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"test_ideation_{topic}"}}

    initial_state = {
        "topic": topic,
        "project_dir": project_dir,
        "output_dir": output_dir,
        "messages": []
    }
    print("Initial Input State:")
    pprint(initial_state)

    print("\nExecuting Ideation Subgraph up to Gate 1 Interrupt...")
    result = await graph.ainvoke(initial_state, config=config)
    print_state_summary("Gate 1 HITL Paused", result)
    return result

async def run_video_production_test(topic: str, project_dir: str, output_dir: str, use_existing: bool = True):
    print_separator("Testing Video Production Subgraph")
    checkpointer = MemorySaver()
    graph = create_video_production_subgraph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"test_video_{topic}"}}

    image_path = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    raw_video_path = _resolve_asset_path(output_dir, topic, "raw_video", next_version=False)

    # Check if a visual plate exists under alternative names (e.g. dog_3d_animation.mp4)
    if use_existing and not (raw_video_path and os.path.exists(raw_video_path)):
        import glob
        cands = glob.glob(os.path.join(output_dir, "*.mp4"))
        if cands:
            raw_video_path = cands[0]
            print(f"Using existing visual plate on disk: {raw_video_path}")

    initial_state = {
        "topic": topic,
        "project_dir": project_dir,
        "output_dir": output_dir,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "raw_video_path": raw_video_path,
        "messages": []
    }
    print("Initial Input State for Video Subgraph:")
    pprint(initial_state)

    print("\nExecuting Video Production Subgraph (Generate -> Remix -> QC)...")
    result = await graph.ainvoke(initial_state, config=config)
    print_state_summary("Video Production Complete", result)
    return result

async def run_copywriting_test(topic: str, project_dir: str, output_dir: str):
    print_separator("Testing Copywriting Subgraph")
    checkpointer = MemorySaver()
    graph = create_copywriting_subgraph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"test_copy_{topic}"}}

    image_path = _resolve_asset_path(output_dir, topic, "image", next_version=False)
    video_plot_path = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    video_path = _resolve_asset_path(output_dir, topic, "video", next_version=False)

    initial_state = {
        "topic": topic,
        "project_dir": project_dir,
        "output_dir": output_dir,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "video_path": video_path,
        "messages": []
    }
    print("Initial Input State for Copywriting Subgraph:")
    pprint(initial_state)

    print("\nExecuting Copywriting Subgraph up to Gate 2 Interrupt...")
    result = await graph.ainvoke(initial_state, config=config)
    print_state_summary("Gate 2 HITL Paused", result)
    return result

async def run_node_remix_directly(topic: str, project_dir: str, output_dir: str):
    print_separator("Testing Remix Node Directly")
    from graphs.content_creation.nodes.remix_video_node import remix_video_node
    
    # Locate candidate raw video
    import glob
    raw_cands = glob.glob(os.path.join(output_dir, f"{topic}*.mp4"))
    raw_video = raw_cands[0] if raw_cands else ""
    video_plot = _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)

    state = {
        "topic": topic,
        "project_dir": project_dir,
        "output_dir": output_dir,
        "raw_video_path": raw_video,
        "video_plot_path": video_plot,
    }
    print("Input state to remix_video_node:")
    pprint(state)

    res = await remix_video_node(state)
    print("\nDirect Output from remix_video_node:")
    pprint(res)
    return res

async def main():
    parser = argparse.ArgumentParser(description="Test and debug Content Creation subgraphs independently.")
    parser.add_argument("--subgraph", choices=["ideation", "video", "copywriting", "remix_node", "all"], default="remix_node",
                        help="Which subgraph or component to run.")
    parser.add_argument("--topic", default="dog", help="Topic / word name (default: dog).")
    parser.add_argument("--project-dir", default="pkm/wiki/software/ayla-first-words", help="Project directory.")
    parser.add_argument("--use-existing", action="store_true", default=True, help="Use existing assets on disk.")

    args = parser.parse_args()
    project_dir = normalize_project_path(args.project_dir)
    output_dir = normalize_project_path(os.path.join(project_dir, "words", args.topic))

    print(f"🚀 Initializing Subgraph Test Runner")
    print(f"📁 Project Dir: {project_dir}")
    print(f"📂 Output Dir:  {output_dir}")
    print(f"🎯 Target:      {args.subgraph}")

    if args.subgraph == "remix_node":
        await run_node_remix_directly(args.topic, project_dir, output_dir)
    elif args.subgraph == "ideation":
        await run_ideation_test(args.topic, project_dir, output_dir)
    elif args.subgraph == "video":
        await run_video_production_test(args.topic, project_dir, output_dir, use_existing=args.use_existing)
    elif args.subgraph == "copywriting":
        await run_copywriting_test(args.topic, project_dir, output_dir)
    elif args.subgraph == "all":
        print("Running all components sequentially...")
        await run_ideation_test(args.topic, project_dir, output_dir)
        await run_video_production_test(args.topic, project_dir, output_dir, use_existing=args.use_existing)
        await run_copywriting_test(args.topic, project_dir, output_dir)

if __name__ == "__main__":
    asyncio.run(main())
