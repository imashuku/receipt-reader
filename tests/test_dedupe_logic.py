import sys
import os
# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.gemini_client import _merge_records, ReceiptRecord, Category, PaymentMethod, TaxRate

def test_dedupe():
    print("Testing deduplication logic...")
    
    # 1. Perfect Match Case
    r1 = ReceiptRecord(
        date="2026/10/01", vendor="Seven Eleven", total_amount=1000,
        invoice_no_norm="T1234567890123", subject="Lunch",
        category=Category.UNKNOWN, payment_method=PaymentMethod.CASH,
        tax_rate_detected=TaxRate.RATE_10, needs_review=False, missing_fields=[],
        segment_id="1", region=None
    )
    # 2. Same date/amount/vendor (normalized), but missing T-num & Subject
    r2 = ReceiptRecord(
        date="2026/10/01", vendor="Seven Eleven Japan", total_amount=1000,
        invoice_no_norm="", subject="",
        category=Category.UNKNOWN, payment_method=PaymentMethod.CASH,
        tax_rate_detected=TaxRate.RATE_10, needs_review=True, missing_fields=["vendor"],
        segment_id="2", region=None
    )
    # 3. Different amount (should not merge)
    r3 = ReceiptRecord(
        date="2026/10/01", vendor="Seven Eleven", total_amount=2000,
        invoice_no_norm="", subject="Dinner",
        category=Category.UNKNOWN, payment_method=PaymentMethod.CASH,
        tax_rate_detected=TaxRate.RATE_10, needs_review=False, missing_fields=[],
        segment_id="3", region=None
    )
    
    print(f"Input records: 3")
    result = _merge_records([r1, r2, r3])
    
    print("Logs:")
    for l in result.logs:
        print("  " + l)
        
    print(f"Result count: {len(result)}")
    if len(result) != 2:
        print(f"FAILED: Expected 2 records, got {len(result)}")
        sys.exit(1)
        
    # Check merged record
    # It should possess the properties of r1 (T-num present)
    merged_r1 = next(r for r in result if r.total_amount == 1000)
    if merged_r1.invoice_no_norm != "T1234567890123":
        print(f"FAILED: Merged record lost T-number. Got: {merged_r1.invoice_no_norm}")
        sys.exit(1)
        
    if merged_r1.vendor != "Seven Eleven": # Should pick the one with better score (r1 has T-num so +30 score)
        print(f"FAILED: Merged record picked wrong vendor. Got: {merged_r1.vendor}")
        # Note: r1 has T-num so score is higher. r2 has missing fields penalty.
        sys.exit(1)
    
    # Check merge metadata
    print(f"Group ID: {merged_r1.group_id}")
    print(f"Merge Reason: {merged_r1.merge_reason}")
    print(f"Candidates: {len(merged_r1.merge_candidates)}")
    
    if not merged_r1.group_id.startswith("fuzzy_"):
        print(f"FAILED: Expected fuzzy group ID, got {merged_r1.group_id}")
        sys.exit(1)
        
    if "Fuzzy Match" not in merged_r1.merge_reason:
        print(f"FAILED: Expected Fuzzy Match reason, got {merged_r1.merge_reason}")
        sys.exit(1)
        
    if len(merged_r1.merge_candidates) != 2:
        print(f"FAILED: Expected 2 candidates, got {len(merged_r1.merge_candidates)}")
        sys.exit(1)

    print("PASSED")

if __name__ == "__main__":
    test_dedupe()
