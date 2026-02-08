
import sys
import os
import pandas as pd
from typing import List
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.models import ReceiptRecord, TaxRate, PaymentMethod, Category
from logic.exporter import revalidate_record, generate_csv_data

def test_workflow():
    print("--- Starting E2E Workflow Test ---")

    # 1. Simulate Analysis Result (3 records)
    # R1: Missing Date (Invalid)
    r1 = ReceiptRecord(
        date="", vendor="Shop A", total_amount=1000,
        invoice_no_norm="T1234567890123", subject="Lunch",
        category=Category.UNKNOWN, payment_method=PaymentMethod.CASH,
        tax_rate_detected=TaxRate.RATE_10, needs_review=True, missing_fields=["date"],
        segment_id="1"
    )
    # R2: Candidate T-Num (Invalid/Candidate)
    r2 = ReceiptRecord(
        date="2026/10/01", vendor="Shop B", total_amount=2000,
        invoice_no_norm="", invoice_candidate="T9876543210987", subject="Dinner",
        category=Category.UNKNOWN, payment_method=PaymentMethod.CASH,
        tax_rate_detected=TaxRate.RATE_10, needs_review=True, missing_fields=["invoice_no_candidate"],
        segment_id="2"
    )
    # R3: Perfect (Valid) but NOT Confirmed initially
    r3 = ReceiptRecord(
        date="2026/10/02", vendor="Shop C", total_amount=3000,
        invoice_no_norm="T1111222233334", subject="Taxi",
        category=Category.TRAVEL, payment_method=PaymentMethod.PAYPAY,
        tax_rate_detected=TaxRate.RATE_10, needs_review=False, missing_fields=[],
        segment_id="3"
    )

    records = [r1, r2, r3]
    print(f"Initial Records: {len(records)}")
    print(f"R1 Valid? {not r1.missing_fields}")
    print(f"R2 Valid? {not r2.missing_fields}")
    print(f"R3 Valid? {not r3.missing_fields}")

    # 2. Simulate User Fixes
    print("\n[Simulate User Fixes]")
    
    # Fix R1 Date
    print("Fixing R1 Date...")
    r1.date = "2026/10/03"
    r1 = revalidate_record(r1)
    print(f"R1 New Status: Valid={not r1.missing_fields}, Confirmed={r1.is_confirmed}")

    # Fix R2 Candidate Confirmation
    print("Confirming R2 Candidate...")
    r2.invoice_no_norm = r2.invoice_candidate
    r2.invoice_candidate = ""
    r2.qualified_flag = "○"
    r2 = revalidate_record(r2)
    print(f"R2 New Status: Valid={not r2.missing_fields}, Confirmed={r2.is_confirmed}")

    # 3. Simulate User Confirmation (Review Done)
    print("\n[Simulate User Confirmation]")
    # User confirms R1 and R2. R3 is left unconfirmed.
    r1.is_confirmed = True
    r2.is_confirmed = True
    
    print(f"R1 Confirmed: {r1.is_confirmed}")
    print(f"R2 Confirmed: {r2.is_confirmed}")
    print(f"R3 Confirmed: {r3.is_confirmed}")

    # 4. Export to CSV (Should contain R1, R2. R3 should be excluded because not confirmed)
    # app.py logic simulation
    valid_confirmed = [r for r in records if r.is_confirmed]
    
    print(f"\nRecords to Export: {len(valid_confirmed)}")
    if len(valid_confirmed) != 2:
        print(f"FAILED: Expected 2 records (R1, R2), got {len(valid_confirmed)}")
        sys.exit(1)
        
    result = generate_csv_data(valid_confirmed)
    print(f"Valid Rows from Exporter: {len(result['valid'])}")
    print(f"Invalid Rows from Exporter: {len(result['invalid'])}")
    
    if len(result['valid']) != 2:
        print(f"FAILED: Exporter rejected some records even though we thought they were valid.")
        for inv in result['invalid']:
             print(f"  Rejected: {inv.get('_error_reasons')}")
        sys.exit(1)

    df = pd.DataFrame(result["valid"])
    out_csv = "test_workflow_export.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
        
    print(f"Exported to {out_csv}")
    
    # Check CSV Content
    df_check = pd.read_csv(out_csv)
    print(f"CSV Rows: {len(df_check)}")
    if len(df_check) != 2:
        print("FAILED: CSV row count mismatch")
        sys.exit(1)
        
    # Check Vendors (column "摘要" starts with vendor)
    vendors = df_check["摘要"].apply(lambda x: x.split()[0] if isinstance(x, str) else "").tolist()
    print(f"Vendors in CSV: {vendors}")
    
    count_ok = 0
    if any("Shop A" in v for v in df_check["摘要"].astype(str)): count_ok += 1
    if any("Shop B" in v for v in df_check["摘要"].astype(str)): count_ok += 1
    
    if count_ok == 2 and "Shop C" not in str(vendors):
        print("SUCCESS: CSV contains correct records.")
    else:
        print(f"FAILED: CSV vendors incorrect.")
        sys.exit(1)
        
    # Cleanup
    if os.path.exists(out_csv):
        os.remove(out_csv)

if __name__ == "__main__":
    test_workflow()
