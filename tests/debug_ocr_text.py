"""
デバッグ用: multi_receipt_01.png のOCRを実行し、
各レシートの ocr_full_text をダンプして T番号の実態を確認する。
"""
import sys
import os
import json
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from logic.gemini_client import (
    _extract_invoice_no_from_text,
    _extract_best_date,
    _T_NUMBER_PATTERN,
    _INVOICE_LABEL_KEYWORDS,
    _parse_response_text,
    _call_openai,
)

IMAGE_PATH = os.path.join(PROJECT_ROOT, "input", "images", "multi_receipt_01.png")

print(f"画像: {IMAGE_PATH}")
print("=" * 70)

# OpenAI で直接呼び出し
print("[INFO] OpenAI GPT-4o でOCR実行中...")
raw = _call_openai(IMAGE_PATH)
items = _parse_response_text(raw)

for i, item in enumerate(items):
    vendor = item.get("vendor", "?")
    ocr_text = item.get("ocr_full_text", "")
    ai_raw = item.get("invoice_no_raw", "")
    ai_date = item.get("date", "")
    
    print(f"\n{'='*70}")
    print(f"  レシート [{i+1}/{len(items)}]: {vendor}")
    print(f"{'='*70}")
    print(f"  AI date:         {ai_date}")
    print(f"  AI invoice_raw:  '{ai_raw}'")
    print(f"  OCR text length: {len(ocr_text)}")
    print(f"\n  ── OCR全文テキスト ──")
    print(f"  {repr(ocr_text)}")
    
    # ラベルキーワード検索
    print(f"\n  ── ラベル検索 ──")
    for kw in _INVOICE_LABEL_KEYWORDS:
        positions = [m.start() for m in re.finditer(re.escape(kw), ocr_text)]
        if positions:
            print(f"    '{kw}' → 位置: {positions}")
            for pos in positions:
                start = max(0, pos - 10)
                end = min(len(ocr_text), pos + len(kw) + 80)
                print(f"      周辺: ...{repr(ocr_text[start:end])}...")
    
    # T番号候補 (全文)
    all_cands = re.findall(_T_NUMBER_PATTERN, ocr_text)
    print(f"\n  ── T番号候補 (全文) ──")
    if all_cands:
        for c in all_cands:
            print(f"    候補: {repr(c.strip())}")
    else:
        print(f"    (候補なし)")
    
    # 抽出結果
    norm, debug, low_conf = _extract_invoice_no_from_text(ocr_text, ai_raw)
    print(f"\n  ── 抽出結果 ──")
    print(f"    T番号:     {norm or '(なし)'}")
    print(f"    デバッグ:  {debug}")
    print(f"    低信頼度:  {low_conf}")
    
    # 日付スコアリング
    best_date, date_debug, date_review = _extract_best_date(ocr_text, ai_date)
    print(f"\n  ── 日付スコアリング ──")
    print(f"    AI日付:    {ai_date}")
    print(f"    ベスト:    {best_date}")
    print(f"    デバッグ:  {date_debug}")
    print(f"    要確認:    {date_review}")

print(f"\n{'='*70}")
print("デバッグ完了")
