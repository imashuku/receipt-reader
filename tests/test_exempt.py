import sys
import os
import unittest
from datetime import datetime

# Adjust path to import logic modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logic.models import ReceiptRecord, TaxRate, Category, PaymentMethod
from logic.exporter import convert_record_to_row, TAX_CLASS_EXEMPT, TAX_RATE_MAP

class TestExemptSupport(unittest.TestCase):
    def test_exempt_tax_rate_mapping(self):
        """TaxRate.EXEMPT mapping check"""
        self.assertEqual(TAX_RATE_MAP[TaxRate.EXEMPT], "0")

    def test_exempt_record_export(self):
        """Check CSV row generation for EXEMPT record"""
        rec = ReceiptRecord(
            date="2026/02/08",
            vendor="NonTaxable Vendor",
            total_amount=1000,
            tax_rate_detected=TaxRate.EXEMPT, # Key: EXEMPT
            category=Category.OTHER,
            payment_method=PaymentMethod.CASH,
            invoice_no_norm=""
        )
        
        row = convert_record_to_row(rec)
        
        # Verify Tax Codes
        self.assertEqual(row["借方消費税区分"], TAX_CLASS_EXEMPT) # Should be "0"
        self.assertEqual(row["借方税率コード"], "0") # Should be "0"
        
        # Verify Amount is passed through
        self.assertEqual(row["借方金額"], "1000")

    def test_taxable_record_export(self):
        """Ensure standard 10% record works as before"""
        rec = ReceiptRecord(
            date="2026/02/08",
            vendor="Taxable Vendor",
            total_amount=1100,
            tax_rate_detected=TaxRate.RATE_10,
            category=Category.OTHER,
            payment_method=PaymentMethod.CASH,
            invoice_no_norm="T1234567890123"
        )
        
        row = convert_record_to_row(rec)
        
        # Verify Tax Codes for standard
        self.assertEqual(row["借方消費税区分"], "2") # Purchase
        self.assertEqual(row["借方税率コード"], "4") # 10%

if __name__ == "__main__":
    unittest.main()
