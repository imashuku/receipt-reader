
try:
    import sys
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    
    print("Importing logic.gemini_client...")
    from logic import gemini_client
    print("Success!")
    
    print("Importing app (syntax check)...")
    # app.py is a script, so importing it might run code.
    # We just want to compile it to check syntax.
    import py_compile
    # We are running from project root, so "app.py" is correct
    app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
    if not os.path.exists(app_path):
        # validation for local run
        app_path = "app.py"
        
    print(f"Compiling {app_path}...")
    py_compile.compile(app_path, doraise=True)
    print("Success!")
    
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
