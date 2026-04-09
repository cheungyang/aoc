import os
import sys
from dotenv import load_dotenv

# Load environment variables from workspace root
load_dotenv("/Users/alvac/dev/langgraph/.env")

# Add workspace root to path
sys.path.append("/Users/alvac/dev/langgraph")

from core.loaders.tools_loader import ToolsLoader

def main():
    print("Loading tools...")
    # ToolsLoader prints "Loaded X tools..." to stderr/stdout, so it will show up.
    loader = ToolsLoader()
    tools = loader.get_tools()
    
    if not tools:
        print("No tools found.")
        return
        
    print("\nAvailable tools:")
    for i, tool in enumerate(tools):
        # LangChain tools have 'name' and 'description'
        name = getattr(tool, 'name', str(tool))
        desc = getattr(tool, 'description', '').split('\n')[0]
        print(f"[{i+1}] {name}: {desc}")
        
    try:
        choice = int(input("\nSelect a tool number to run: ")) - 1
        if choice < 0 or choice >= len(tools):
            print("Invalid choice.")
            return
    except ValueError:
        print("Invalid input. Please enter a number.")
        return
        
    selected_tool = tools[choice]
    print(f"\nSelected Tool: {selected_tool.name}")
    print(f"Full Description: {selected_tool.description}")
    
    args = getattr(selected_tool, 'args', {})
    
    print("\nArguments needed:")
    input_args = {}
    
    for arg_name, arg_info in args.items():
        arg_type = arg_info.get('type', 'string')
        arg_desc = arg_info.get('description', '')
        
        user_val = input(f"  Enter {arg_name} ({arg_type}) [{arg_desc}]: ")
        
        if user_val.strip():
             input_args[arg_name] = user_val
             
    print(f"\nInvoking {selected_tool.name} with args: {input_args}")
    try:
        # Use invoke for LangChain tools
        result = selected_tool.invoke(input_args)
        print(f"\n==== Result ====\n{result}\n================")
    except Exception as e:
        print(f"Error executing tool: {e}")

if __name__ == "__main__":
    main()
