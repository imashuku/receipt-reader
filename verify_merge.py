
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logic.gemini_client import analyze_receipt_image

def verify_multi_02():
    img_path = "input/images/multi_receipt_02.jpg"
    if not os.path.exists(img_path):
        print(f"Image not found: {img_path}")
        return

    print(f"Analyzing {img_path} with split scan...")
    result = analyze_receipt_image(img_path, use_split_scan=True)
    
    print(f"Total Records: {len(result)}")
    print(f"Raw Records: {len(result.raw_records)}")
    
    # Validation
    if 7 <= len(result) <= 9:
        print("SUCCESS: Record count is within target range (8±1)")
    else:
        print(f"WARNING: Record count {len(result)} is outside target range (8±1)")

    # Show merge breakdown
    for r in result:
        if r.merge_candidates:
            print(f"- Merged {len(r.merge_candidates)} records. Reason: {r.merge_reason}")
            print(f"  Representative: {r.vendor} ({r.total_amount})")

if __name__ == "__main__":
    verify_multi_02()
