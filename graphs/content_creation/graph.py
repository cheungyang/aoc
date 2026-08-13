import os
import json
import asyncio
import operator
from typing import TypedDict, List, Dict, Any, Annotated, Sequence, Optional
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from core.loaders.agents_loader import AgentsLoader
from tools.dalle_image_generator import dalle_image_generator
from tools.runway_video_animator import runway_video_animator

class ContentCreationState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    query: str
    topic: str
    character_sheet_path: str
    character_sheet_content: str
    output_dir: str
    draft_prompts: List[str]
    editor_image_feedback: str
    image_prompts_approved: bool
    image_review_attempts: int
    max_image_reviews: int
    generated_images: List[str]
    selected_image_index: int
    selected_image_path: str
    draft_motion_prompt: str
    editor_motion_feedback: str
    motion_prompt_approved: bool
    motion_review_attempts: int
    max_motion_reviews: int
    generated_video_path: str
    draft_caption: str
    final_caption: str
    session_id: str
    error_message: str

def _load_character_sheet(sheet_path: str) -> str:
    """Helper to load character sheet from file path if it exists."""
    if sheet_path and os.path.isfile(sheet_path):
        try:
            with open(sheet_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"ContentCreationGraph: Failed to read character sheet at {sheet_path}: {e}")
    return "Character: 3D Pixar/Disney style toddler-friendly character with vibrant colors and expressive eyes."

async def draft_image_prompts_node(state: ContentCreationState):
    """Step 1: Content Creator drafts 5 DALL-E image prompts."""
    topic = state.get("topic") or state.get("query") or "Delightful toddler adventure"
    sheet_content = state.get("character_sheet_content") or _load_character_sheet(state.get("character_sheet_path", ""))
    feedback = state.get("editor_image_feedback", "")

    creator = AgentsLoader().get_agent("content-creator")
    
    prompt = (
        f"You are the Content Creator. Based on the topic '{topic}' and the character constraints:\n"
        f"--- Character Sheet ---\n{sheet_content}\n-----------------------\n"
    )
    if feedback:
        prompt += f"\nPrevious Brand Editor Feedback to address:\n{feedback}\n\n"
    
    prompt += (
        f"Draft exactly 5 distinct, highly creative, Pixar/Disney 3D style DALL-E image prompts.\n"
        f"Each prompt must strictly maintain the character likeness and feature the topic.\n"
        f"Output your 5 prompts strictly as a valid JSON list of 5 strings:\n"
        f'[\n'
        f'  "Prompt 1...",\n'
        f'  "Prompt 2...",\n'
        f'  "Prompt 3...",\n'
        f'  "Prompt 4...",\n'
        f'  "Prompt 5..."\n'
        f']\n'
        f"Do not include any explanation or markdown formatting outside the JSON list."
    )

    response = await creator.execute(prompt, source="subgraph")
    cleaned = response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        drafts = json.loads(cleaned)
        if not isinstance(drafts, list) or len(drafts) == 0:
            raise ValueError("Parsed JSON is not a non-empty list")
        # Ensure we have strings and limit to 5
        drafts = [str(p) for p in drafts[:5]]
    except Exception as e:
        print(f"ContentCreationGraph: Error parsing image prompts JSON ({e}). Falling back to line splitting.")
        drafts = [line.strip().lstrip("1234567890.- ") for line in response.split("\n") if line.strip() and len(line.strip()) > 10][:5]
        if len(drafts) < 5:
            while len(drafts) < 5:
                drafts.append(f"{sheet_content}. Scene {len(drafts) + 1} exploring {topic}, 3D Disney Pixar animation style.")

    return {
        "character_sheet_content": sheet_content,
        "draft_prompts": drafts,
        "topic": topic
    }

async def review_image_prompts_node(state: ContentCreationState):
    """Step 2: Brand Editor reviews and critiques the 5 image prompts."""
    drafts = state.get("draft_prompts", [])
    topic = state.get("topic", "")
    sheet_content = state.get("character_sheet_content", "")
    attempts = state.get("image_review_attempts", 0) + 1
    max_reviews = state.get("max_image_reviews", 3)

    editor = AgentsLoader().get_agent("brand-editor")

    prompts_text = "\n".join([f"{i+1}. {p}" for i, p in enumerate(drafts)])
    prompt = (
        f"You are the Brand Editor (Red Team QC). Review the following 5 DALL-E image prompts drafted for topic '{topic}':\n"
        f"--- Character Sheet Constraints ---\n{sheet_content}\n-----------------------------------\n"
        f"--- Draft Prompts ---\n{prompts_text}\n---------------------\n\n"
        f"Verify character consistency, style compliance (3D Pixar/Disney), and appropriateness for toddlers.\n"
        f"If ALL 5 prompts meet the standard, respond with 'VERDICT: APPROVED' on the first line, followed by a brief summary.\n"
        f"If ANY prompt needs revision, respond with 'VERDICT: REJECTED' on the first line, followed by specific actionable instructions for the creator to fix them."
    )

    response = await editor.execute(prompt, source="subgraph")
    is_approved = "VERDICT: APPROVED" in response.upper() or ("APPROVED" in response.upper() and "REJECTED" not in response.upper())

    feedback = "" if is_approved else response

    return {
        "image_prompts_approved": is_approved,
        "editor_image_feedback": feedback,
        "image_review_attempts": attempts
    }

def should_continue_image_review(state: ContentCreationState):
    """Router: proceeds to image generation if approved or max attempts reached."""
    if state.get("image_prompts_approved"):
        return "generate_images"
    if state.get("image_review_attempts", 0) >= state.get("max_image_reviews", 3):
        print("ContentCreationGraph: Max image review attempts reached. Proceeding to generation.")
        return "generate_images"
    return "draft_prompts"

async def generate_images_node(state: ContentCreationState):
    """Step 3: Content Creator executes dalle_image_generator for the 5 prompts."""
    drafts = state.get("draft_prompts", [])
    output_dir = state.get("output_dir") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "ayla-first-words", state.get("session_id", "session_1")))
    os.makedirs(output_dir, exist_ok=True)

    generated_paths = []
    for i, prompt_text in enumerate(drafts[:5]):
        target_path = os.path.join(output_dir, f"scene_{i+1}.png")
        print(f"ContentCreationGraph: Generating image {i+1}/5 at {target_path}...")
        try:
            result = await dalle_image_generator.ainvoke({
                "prompt": prompt_text,
                "output_path": target_path,
                "agent_id": "content-creator"
            })
            if "<payload>" in result and "</payload>" in result:
                saved_path = result.split("<payload>")[1].split("</payload>")[0].strip()
                if saved_path:
                    generated_paths.append(saved_path)
                else:
                    generated_paths.append(target_path)
            else:
                generated_paths.append(target_path)
        except Exception as e:
            print(f"ContentCreationGraph: Error generating image {i+1}: {e}")
            generated_paths.append(target_path)

    return {
        "generated_images": generated_paths,
        "output_dir": output_dir
    }

async def select_image_node(state: ContentCreationState):
    """Step 4: Human-in-the-Loop selection node (resumed after interrupt_before)."""
    images = state.get("generated_images", [])
    selected_path = state.get("selected_image_path")
    selected_index = state.get("selected_image_index")

    if not selected_path:
        if selected_index is not None and 0 <= selected_index < len(images):
            selected_path = images[selected_index]
        elif images:
            selected_path = images[0]
        else:
            selected_path = ""

    print(f"ContentCreationGraph: Selected image for animation is '{selected_path}'")
    return {
        "selected_image_path": selected_path
    }

async def draft_motion_prompt_node(state: ContentCreationState):
    """Step 5: Content Creator drafts a Runway Gen-3 motion prompt."""
    selected_image = state.get("selected_image_path", "")
    topic = state.get("topic", "")
    feedback = state.get("editor_motion_feedback", "")

    creator = AgentsLoader().get_agent("content-creator")
    prompt = (
        f"You are the Content Creator. We have selected the image at '{selected_image}' for the Toddler Tales story about '{topic}'.\n"
        f"Draft a subtle, cinematic Runway Gen-3 motion prompt for animating this static image.\n"
        f"Guidelines:\n"
        f"- Focus on gentle character movement (e.g. blinking, smiling, waving) and slow camera push-in/pan.\n"
        f"- Avoid extreme movements, fast morphing, or complex physics that cause Gen-3 distortions.\n"
    )
    if feedback:
        prompt += f"\nPrevious Brand Editor Feedback to address:\n{feedback}\n"

    prompt += "\nOutput your motion prompt clearly and concisely without unnecessary surrounding text."

    response = await creator.execute(prompt, source="subgraph")
    return {
        "draft_motion_prompt": response.strip()
    }

async def review_motion_prompt_node(state: ContentCreationState):
    """Step 6: Brand Editor reviews the motion prompt to enforce Gen-3 rules and save API costs."""
    motion_prompt = state.get("draft_motion_prompt", "")
    selected_image = state.get("selected_image_path", "")
    attempts = state.get("motion_review_attempts", 0) + 1

    editor = AgentsLoader().get_agent("brand-editor")
    prompt = (
        f"You are the Brand Editor (Red Team QC). Review the following Runway Gen-3 motion prompt:\n"
        f"Image: '{selected_image}'\n"
        f"Motion Prompt: '{motion_prompt}'\n\n"
        f"Enforce strict Runway Gen-3 guardrails to protect API budget and prevent hallucinations:\n"
        f"1. Reject high-velocity motions or complex physics interactions.\n"
        f"2. Ensure motion prompt is concise and guides smooth, subtle animations.\n\n"
        f"If the prompt meets all standards, respond with 'VERDICT: APPROVED' on the first line.\n"
        f"If the prompt is risky or flawed, respond with 'VERDICT: REJECTED' on the first line followed by specific required changes."
    )

    response = await editor.execute(prompt, source="subgraph")
    is_approved = "VERDICT: APPROVED" in response.upper() or ("APPROVED" in response.upper() and "REJECTED" not in response.upper())
    feedback = "" if is_approved else response

    return {
        "motion_prompt_approved": is_approved,
        "editor_motion_feedback": feedback,
        "motion_review_attempts": attempts
    }

def should_continue_motion_review(state: ContentCreationState):
    """Router: proceeds to video generation if approved or max attempts reached."""
    if state.get("motion_prompt_approved"):
        return "generate_video"
    if state.get("motion_review_attempts", 0) >= state.get("max_motion_reviews", 3):
        print("ContentCreationGraph: Max motion review attempts reached. Proceeding to video generation.")
        return "generate_video"
    return "draft_motion"

async def generate_video_node(state: ContentCreationState):
    """Step 7: Content Creator executes runway_video_animator."""
    selected_image = state.get("selected_image_path", "")
    motion_prompt = state.get("draft_motion_prompt", "Slow camera push in, gentle character smile and movement")
    output_dir = state.get("output_dir") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "ayla-first-words", state.get("session_id", "session_1")))
    video_output_path = os.path.join(output_dir, "animated_story.mp4")

    print(f"ContentCreationGraph: Generating Runway video from {selected_image} to {video_output_path}...")
    try:
        result = await runway_video_animator.ainvoke({
            "prompt_text": motion_prompt,
            "image_path": selected_image,
            "output_path": video_output_path,
            "agent_id": "content-creator"
        })
        if "<payload>" in result and "</payload>" in result:
            saved_path = result.split("<payload>")[1].split("</payload>")[0].strip()
            video_path = saved_path or video_output_path
        else:
            video_path = video_output_path
    except Exception as e:
        print(f"ContentCreationGraph: Error generating video: {e}")
        video_path = video_output_path

    return {
        "generated_video_path": video_path
    }

async def draft_and_polish_copy_node(state: ContentCreationState):
    """Steps 8 & 9: Creator drafts Instagram caption, Editor polishes for virality."""
    topic = state.get("topic", "")
    creator = AgentsLoader().get_agent("content-creator")
    editor = AgentsLoader().get_agent("brand-editor")

    # Step 8: Creator drafts copy
    creator_prompt = (
        f"You are the Content Creator. Draft an engaging, parent-friendly Instagram caption for our Toddler Tales video on '{topic}'.\n"
        f"Include a catchy hook, a 2-sentence story summary, a question to drive comments, and popular toddler/parenting hashtags."
    )
    draft_copy = await creator.execute(creator_prompt, source="subgraph")

    # Step 9: Editor polishes copy
    editor_prompt = (
        f"You are the Brand Editor. Polish the following Instagram caption for maximum engagement, optimal emoji placement, and viral appeal:\n"
        f"--- Draft Caption ---\n{draft_copy}\n---------------------\n"
        f"Output the final polished caption directly."
    )
    polished_copy = await editor.execute(editor_prompt, source="subgraph")

    return {
        "draft_caption": draft_copy.strip(),
        "final_caption": polished_copy.strip()
    }

async def finalize_delivery_node(state: ContentCreationState):
    """Step 10: Final delivery summarizing all assets and delivering response."""
    images = state.get("generated_images", [])
    video_path = state.get("generated_video_path", "")
    selected_image = state.get("selected_image_path", "")
    caption = state.get("final_caption", "")
    topic = state.get("topic", "")

    images_xml = "\n".join([f'  <image path="{img}"/>' for img in images])

    response_text = (
        f"🎉 **Toddler Tales Content Creation Complete!**\n\n"
        f"**Topic**: {topic}\n\n"
        f"### 🖼️ Generated Images (5)\n"
        + "\n".join([f"- Image {i+1}: `{img}`" for i, img in enumerate(images)]) + "\n\n"
        f"**Selected Image for Animation**: `{selected_image}`\n\n"
        f"### 🎬 Animated Video\n"
        f"- Video File: `{video_path}`\n\n"
        f"### 📱 Polished Instagram Caption\n"
        f"```\n{caption}\n```\n\n"
        f"<images>\n{images_xml}\n</images>"
    )

    return {
        "messages": [AIMessage(content=response_text)]
    }

def create_graph(checkpointer=None, **kwargs):
    """Compiles and returns the Toddler Tales content creation graph with human-in-the-loop pause."""
    workflow = StateGraph(ContentCreationState)

    # 1. Add nodes
    workflow.add_node("draft_prompts", draft_image_prompts_node)
    workflow.add_node("review_prompts", review_image_prompts_node)
    workflow.add_node("generate_images", generate_images_node)
    workflow.add_node("select_image", select_image_node)
    workflow.add_node("draft_motion", draft_motion_prompt_node)
    workflow.add_node("review_motion", review_motion_prompt_node)
    workflow.add_node("generate_video", generate_video_node)
    workflow.add_node("draft_and_polish_copy", draft_and_polish_copy_node)
    workflow.add_node("finalize_delivery", finalize_delivery_node)

    # 2. Add edges
    workflow.add_edge(START, "draft_prompts")
    workflow.add_edge("draft_prompts", "review_prompts")

    workflow.add_conditional_edges(
        "review_prompts",
        should_continue_image_review,
        {
            "draft_prompts": "draft_prompts",
            "generate_images": "generate_images"
        }
    )

    # Human-in-the-loop pause right after the 5 images are generated
    workflow.add_edge("generate_images", "select_image")
    workflow.add_edge("select_image", "draft_motion")
    workflow.add_edge("draft_motion", "review_motion")

    workflow.add_conditional_edges(
        "review_motion",
        should_continue_motion_review,
        {
            "draft_motion": "draft_motion",
            "generate_video": "generate_video"
        }
    )

    workflow.add_edge("generate_video", "draft_and_polish_copy")
    workflow.add_edge("draft_and_polish_copy", "finalize_delivery")
    workflow.add_edge("finalize_delivery", END)

    # Compile with interrupt_before right after 5 images are generated
    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["select_image"]
    )

# Backward-compatible default compiled instance
graph = create_graph()

def prepare_input(query: str, caller: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Translates incoming text query into initial ContentCreationState."""
    if caller and "<caller>" not in query:
        formatted_query = f"<caller>{caller}</caller>\n{query}"
    else:
        formatted_query = query

    session_id = kwargs.get("session_id", "toddler_session_1")
    return {
        "messages": [{"role": "user", "content": formatted_query}],
        "query": formatted_query,
        "topic": kwargs.get("topic", query),
        "character_sheet_path": kwargs.get("character_sheet_path", ""),
        "character_sheet_content": kwargs.get("character_sheet_content", ""),
        "output_dir": kwargs.get("output_dir", ""),
        "draft_prompts": [],
        "editor_image_feedback": "",
        "image_prompts_approved": False,
        "image_review_attempts": 0,
        "max_image_reviews": kwargs.get("max_image_reviews", 3),
        "generated_images": [],
        "selected_image_index": kwargs.get("selected_image_index", 0),
        "selected_image_path": kwargs.get("selected_image_path", ""),
        "draft_motion_prompt": "",
        "editor_motion_feedback": "",
        "motion_prompt_approved": False,
        "motion_review_attempts": 0,
        "max_motion_reviews": kwargs.get("max_motion_reviews", 3),
        "generated_video_path": "",
        "draft_caption": "",
        "final_caption": "",
        "session_id": session_id,
        "error_message": ""
    }

def format_output(state: Dict[str, Any]) -> str:
    """Extracts final reply text from ContentCreationState."""
    if isinstance(state, dict):
        if "messages" in state and state["messages"]:
            return state["messages"][-1].content
        if state.get("error_message"):
            return f"Content creation failed: {state['error_message']}"
    return str(state)
