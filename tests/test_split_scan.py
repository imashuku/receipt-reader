
import os
import sys
import tempfile
import shutil
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from logic.gemini_client import _split_image

def test_split_image():
    # Create a dummy image
    img_path = "test_dummy.jpg"
    img = Image.new("RGB", (100, 100), color="red")
    img.save(img_path)
    
    try:
        results = _split_image(img_path)
        print(f"Created {len(results)} splits.")
        
        for path, offset in results:
            print(f"Split path: {path}")
            if not path.startswith(tempfile.gettempdir()) and not "/tmp/" in path and not "var/folders" in path:
                print(f"ERROR: Split file created outside of temp dir: {path}")
                sys.exit(1)
            
            if not os.path.exists(path):
                print(f"ERROR: Split file does not exist: {path}")
                sys.exit(1)
                
            # Clean up individually (as the real code does)
            os.remove(path)
            
        print("Success: Splits created in temp dir.")
        
    finally:
        if os.path.exists(img_path):
            os.remove(img_path)

if __name__ == "__main__":
    test_split_image()
