
import sys
import os
import unittest

# プロジェクトルートにパスを通す
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.gemini_client import _extract_invoice_no_from_text

class TestBlockMerge(unittest.TestCase):
    def test_merge_separated_blocks(self):
        # ケース1: 空白で完全に分かれている (合計13桁)
        # 既存正規表現(\s許可)で拾われるので low_conf=False になるはず
        text = "登録番号 T 123 456 789 0123 です。"
        norm, debug, low_conf = _extract_invoice_no_from_text(text, "")
        print(f"Case 1: {norm} ({debug})")
        self.assertEqual(norm, "T1234567890123")
        self.assertFalse(low_conf) 

    def test_merge_fallback_trigger(self):
        # ケース1.5: 正規表現に含まれない文字（_）が挟まる -> 既存ロジック失敗 -> フォールバック発動
        text = "登録番号 T_123_456_789_0123"
        norm, debug, low_conf = _extract_invoice_no_from_text(text, "")
        print(f"Case 1.5: {norm} ({debug})")
        self.assertEqual(norm, "T1234567890123")
        self.assertTrue(low_conf) # candidate扱い

    def test_merge_partial_blocks(self):
        # ケース2: 一部が結合されている
        text = "登録番号 T123 4567 890123"
        norm, debug, low_conf = _extract_invoice_no_from_text(text, "")
        print(f"Case 2: {norm} ({debug})")
        self.assertEqual(norm, "T1234567890123")

    def test_merge_with_noise(self):
        # ケース3: 間に改行
        text = "登録番号\nT\n123\n456\n789\n0123"
        norm, debug, low_conf = _extract_invoice_no_from_text(text, "")
        print(f"Case 3: {norm} ({debug})")
        self.assertEqual(norm, "T1234567890123")
    
    def test_merge_12_digits_fallback(self):
         # ケース4: 12桁しかない（T + 12桁 → 0補完）
        text = "登録番号 T_123_456_789_012" # _を入れてフォールバック発動させる
        norm, debug, low_conf = _extract_invoice_no_from_text(text, "")
        print(f"Case 4: {norm} ({debug})")
        self.assertEqual(norm, "T0123456789012")
        self.assertTrue(low_conf)

    def test_merge_long_distance(self):
        # ケース5: 間に無視される文字が多数あっても、ウィンドウ内(max8トークン)なら結合される
        text = "登録番号 T a b c d 1234567890123"
        # tokens: [T, 123...] -> 隣接扱い
        norm, debug, low_conf = _extract_invoice_no_from_text(text, "")
        print(f"Case 5: '{norm}' ({debug})")
        self.assertEqual(norm, "T1234567890123") 

if __name__ == "__main__":
    unittest.main()
