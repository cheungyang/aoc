# AGENTS.md

## Operating Instructions
1. **Start a run**: map the project to its `project_path` and `output_path` from the Content projects list in your human context, put the topic where the output path has a placeholder, and call `graph_call(graph_name="content_creation", query="topic: <topic>, project_path: <path>, output_path: <path>")`. If the project or the topic is unclear, ask first: every run pays for renders.
2. **At a gate**: a run pauses twice, at Gate 1 (image and plot) and at Gate 2 (the finished package). Post the gate card exactly as the graph returned it, its `<images>` and `<videos>` blocks included: they attach the media. Pass the user's next reply to `graph_call(graph_name="content_creation", query=<their words>)` unchanged; the graph reads it. Never interpret, shorten or answer it for them.
3. **Unsure whether a run is waiting?** Call `graph_status(graph_name="content_creation")` before starting another.
