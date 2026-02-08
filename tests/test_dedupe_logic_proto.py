
import unittest
from datetime import datetime
from typing import List, Optional
import hashlib
import re

# Mocking the models for standalone testing
class ReceiptRecord:
    def __init__(self, date, total_amount, vendor, subject, invoice_no_norm, invoice_candidate, tax_rate_detected, payment_method, needs_review, missing_fields, ocr_full_text="", region=None):
        self.date = date
        self.total_amount = total_amount
        self.vendor = vendor
        self.subject = subject
        self.invoice_no_norm = invoice_no_norm
        self.invoice_candidate = invoice_candidate
        self.tax_rate_detected = tax_rate_detected
        self.payment_method = payment_method
        self.needs_review = needs_review
        self.missing_fields = missing_fields
        self.ocr_full_text = ocr_full_text
        self.region = region

    def __repr__(self):
        return f"Record(date={self.date}, amt={self.total_amount}, vnd={self.vendor}, score={calculate_score(self)})"

def _normalize_text(text: str) -> str:
    if not text:
        return ""
    # Simple normalization: lowercase, remove spaces, remove special chars
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^a-z0-9]", "", text) # very aggressive for fingerprint
    return text

def _fingerprint_text(text: str) -> str:
    norm = _normalize_text(text)
    if len(norm) < 10: # Too short to be unique
        return ""
    return hashlib.md5(norm.encode('utf-8')).hexdigest()

def calculate_score(record: ReceiptRecord) -> int:
    score = 0
    if record.invoice_no_norm:
        score += 30
    if record.invoice_candidate:
        score += 10
    if str(record.tax_rate_detected) != "unknown" and str(record.tax_rate_detected) != "TaxRate.UNKNOWN":
         score += 10
    if str(record.payment_method) != "unknown" and str(record.payment_method) != "PaymentMethod.UNKNOWN":
        score += 10
    if record.vendor and record.vendor != "?":
        score += 10
    
    score -= (len(record.missing_fields) * 5)
    return score

def get_group_key(record: ReceiptRecord):
    # Prepare normalized values
    d = record.date if record.date else "unknown"
    a = str(record.total_amount) if record.total_amount else "unknown"
    v = record.vendor if record.vendor and record.vendor != "?" else "unknown"
    s = record.subject if record.subject else "unknown"
    
    # 1. (date, total_amount, vendor_norm)
    if d != "unknown" and a != "unknown" and v != "unknown":
        return f"KEY1:{d}_{a}_{v}"
    
    # 2. (date, total_amount, subject_norm) - if vendor unknown
    if d != "unknown" and a != "unknown" and v == "unknown" and s != "unknown":
        return f"KEY2:{d}_{a}_{s}"
        
    # 3. (total_amount, vendor_norm, subject_norm) - if date unknown
    if d == "unknown" and a != "unknown" and v != "unknown" and s != "unknown":
        return f"KEY3:{a}_{v}_{s}"
        
    # 4. Fingerprint
    fp = _fingerprint_text(record.ocr_full_text)
    if fp:
        return f"KEY4:{fp}"
        
    # Fallback: unique object id (no merge)
    return f"UNIQUE:{id(record)}"

def merge_records_logic(records: List[ReceiptRecord]) -> List[ReceiptRecord]:
    if not records:
        return []
        
    # Grouping
    groups = {}
    for r in records:
        key = get_group_key(r)
        if key not in groups:
            groups[key] = []
        groups[key].append(r)
        
    print(f"DEBUG: Created {len(groups)} groups from {len(records)} records.")
    for k, v in groups.items():
        print(f"  Key: {k} -> {len(v)} items")

    merged_results = []
    
    for key, group_records in groups.items():
        if len(group_records) == 1:
            merged_results.append(group_records[0])
            continue
            
        # Select representative
        # Sort by score desc
        group_records.sort(key=lambda r: calculate_score(r), reverse=True)
        best = group_records[0]
        
        print(f"  Merging group {key}: Best score {calculate_score(best)}")
        
        # Merge logic
        # - Invoice No: confirmed wins
        # - Candidate: keep if exists (maybe aggregate candidates?)
        # - Needs Review: OR
        
        final_invoice_confirmed = best.invoice_no_norm
        final_invoice_candidate = best.invoice_candidate
        
        # Check if any other record has a confirmed invoice no (unlikely if best doesn't, given the score, but possible)
        if not final_invoice_confirmed:
            for r in group_records:
                if r.invoice_no_norm:
                    final_invoice_confirmed = r.invoice_no_norm
                    break
        
        # Needs Review OR
        final_needs_review = any(r.needs_review for r in group_records)
        
        # Create merged record (clone best)
        # Assuming we just modify 'best' or create new. Let's modify 'best' copy.
        best.invoice_no_norm = final_invoice_confirmed
        best.needs_review = final_needs_review
        # best.invoice_candidate is already from best. 
        # If best didn't have candidate but others did? 
        if not best.invoice_candidate:
             for r in group_records:
                 if r.invoice_candidate:
                     best.invoice_candidate = r.invoice_candidate
                     break
        
        merged_results.append(best)
        
    return merged_results

class TestDedupe(unittest.TestCase):
    def test_dedupe_scenario_1(self):
        # Scenario: Same receipt, one perfect, one partial
        r1 = ReceiptRecord("2026/02/09", 1000, "DonKi", "Goods", "", "", "10", "cash", False, [], "full text donki 1000 yen 2026/02/09")
        r2 = ReceiptRecord("2026/02/09", 1000, "DonKi", "Goods", "", "", "unknown", "unknown", True, ["tax_rate"], "full text donki 1000 yen 2026/02/09 partial")
        
        records = [r1, r2]
        merged = merge_records_logic(records)
        
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].vendor, "DonKi")
        # needs_review is ORed -> True because r2 was True
        self.assertTrue(merged[0].needs_review) 
        
    def test_dedupe_scenario_2_diff_vendors(self):
        # Different items, should not merge
        r1 = ReceiptRecord("2026/02/09", 1000, "DonKi", "Goods", "", "", "10", "cash", False, [])
        r2 = ReceiptRecord("2026/02/10", 2000, "7-11", "Food", "", "", "8", "cash", False, [])
        
        records = [r1, r2]
        merged = merge_records_logic(records)
        self.assertEqual(len(merged), 2)

if __name__ == "__main__":
    unittest.main()
