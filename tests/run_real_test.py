"""
実画像バッチテスト: input/images/ 配下の画像を1枚ずつ処理

使い方:
  # 特定画像のみ処理
  python tests/run_real_test.py multi_receipt_01.png

  # input/images/ 配下を全件処理
  python tests/run_real_test.py

出力:
  output/<ファイル名>/
    ├── log.txt          # 実行ログ
    ├── result.csv       # CSV出力 (valid分のみ)
    └── summary.json     # 抽出結果サマリー (JSON)
"""
import sys
import os
import csv
import json
import glob
from datetime import datetime

# プロジェクトルート
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from logic.gemini_client import analyze_receipt_image
from logic.exporter import generate_csv_data, validate_mandatory_fields, convert_record_to_row
from logic.models import ReceiptRecord, Category, PaymentMethod, TaxRate

INPUT_DIR = os.path.join(PROJECT_ROOT, "input", "images")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# 対応する画像拡張子
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".bmp"}


class Logger:
    """ファイル + コンソール 両方にログ出力"""
    def __init__(self, log_path: str):
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.f = open(log_path, "w", encoding="utf-8")

    def log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        self.f.write(line + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def process_single_image(image_path: str, use_split: bool = False):
    """1枚の画像に対してE2Eパイプラインを実行しログを残す"""
    filename = os.path.basename(image_path)
    stem = os.path.splitext(filename)[0]

    # 出力フォルダ
    out_dir = os.path.join(OUTPUT_DIR, stem)
    os.makedirs(out_dir, exist_ok=True)

    logger = Logger(os.path.join(out_dir, "log.txt"))

    logger.log("=" * 60)
    logger.log(f"  実画像テスト: {filename}")
    logger.log(f"  パス: {image_path}")
    logger.log("=" * 60)

    # ── Step 1: OCR ──
    logger.log("")
    logger.log("[Step 1] OCR実行中...")
    try:
        records = analyze_receipt_image(image_path, use_split_scan=use_split)
    except Exception as e:
        logger.log(f"[FATAL] OCR実行エラー: {type(e).__name__}: {e}")
        logger.close()
        return {"file": filename, "status": "ERROR", "error": str(e)}

    # マージログ出力
    if hasattr(records, "logs") and records.logs:
        logger.log("")
        logger.log("── マージログ ──")
        for l in records.logs:
            logger.log(f"  {l}")
        logger.log("────────────────")

    if not records:
        logger.log("[WARN] レシートが1件も抽出されませんでした")
        logger.close()
        return {"file": filename, "status": "NO_RECEIPTS", "count": 0}

    logger.log(f"[OK] {len(records)} 件のレシートを抽出")
    logger.log("")

    # 各レコード詳細をログ
    for i, rec in enumerate(records):
        logger.log(f"  ── レシート [{i+1}/{len(records)}] ──")
        logger.log(f"    日付:       {rec.date}")
        logger.log(f"    支払先:     {rec.vendor}")
        logger.log(f"    件名:       {rec.subject}")
        logger.log(f"    税込総額:   ¥{rec.total_amount:,}")
        logger.log(f"    税率:       {rec.tax_rate_detected.value}")
        logger.log(f"    支払方法:   {rec.payment_method.value}")
        logger.log(f"    T番号:      {rec.invoice_no_norm or '(なし)'}")
        if rec.invoice_candidate:
            logger.log(f"    T番号候補:  {rec.invoice_candidate} (要確認)")
        logger.log(f"    適格事業者: {rec.qualified_flag or '(なし)'}")
        logger.log(f"    カテゴリ:   {rec.category.value}")
        logger.log(f"    要確認:     {rec.needs_review}")
        if rec.missing_fields:
            logger.log(f"    不足項目:   {rec.missing_fields}")
        logger.log("")

    # ── Step 2: CSV変換 & バリデーション ──
    logger.log("[Step 2] CSV変換 & 必須項目チェック...")

    # テスト用補完: category/payment が UNKNOWN のままだと勘定科目コード空 → CSV除外
    # 実運用ではUI側でユーザーが設定する
    for rec in records:
        if rec.category == Category.UNKNOWN:
            rec.category = Category.OTHER
            logger.log(f"  [補完] {rec.vendor}: カテゴリ未設定 → 雑費(OTHER)")
        if rec.payment_method == PaymentMethod.UNKNOWN:
            rec.payment_method = PaymentMethod.CASH
            logger.log(f"  [補完] {rec.vendor}: 支払方法未設定 → 現金(CASH)")

    result = generate_csv_data(records)
    valid = result["valid"]
    invalid = result["invalid"]

    logger.log(f"  Valid:   {len(valid)} 件")
    logger.log(f"  Invalid: {len(invalid)} 件")

    for inv in invalid:
        logger.log(f"  [NG] 不足項目: {inv['_error_reasons']}")
    logger.log("")

    # ── Step 3: CSV書き出し ──
    csv_path = os.path.join(out_dir, "result.csv")
    if valid:
        headers = [k for k in valid[0].keys() if not k.startswith("_")]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in valid:
                writer.writerow({k: v for k, v in row.items() if not k.startswith("_")})
        logger.log(f"[Step 3] CSV出力: {csv_path}")
        logger.log("")
        logger.log("── CSV内容 ──")
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                logger.log(f"  {line.rstrip()}")
    else:
        logger.log("[Step 3] 有効レコード0件のためCSV出力スキップ")

    # ── Step 4: サマリーJSON ──
    summary = {
        "file": filename,
        "timestamp": datetime.now().isoformat(),
        "total_receipts": len(records),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "records": [
            {
                "date": r.date,
                "vendor": r.vendor,
                "subject": r.subject,
                "total_amount": r.total_amount,
                "tax_rate": r.tax_rate_detected.value,
                "payment_method": r.payment_method.value,
                "invoice_no": r.invoice_no_norm,
                "invoice_candidate": r.invoice_candidate,
                "category": r.category.value,
                "needs_review": r.needs_review,
                "missing_fields": r.missing_fields,
            }
            for r in records
        ],
        "invalid_reasons": [
            inv["_error_reasons"] for inv in invalid
        ],
    }
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.log("")
    logger.log(f"[Step 4] サマリーJSON: {summary_path}")

    # ── 結果 ──
    logger.log("")
    logger.log("=" * 60)
    if len(valid) > 0:
        logger.log(f"  ✅ {filename}: PASSED ({len(valid)} valid / {len(invalid)} invalid)")
    else:
        logger.log(f"  ❌ {filename}: FAILED (有効レコード0件)")
    logger.log("=" * 60)

    logger.close()
    return summary


def main():
    """メインエントリーポイント"""
    # 引数チェック
    use_split = "--split" in sys.argv
    if use_split:
        sys.argv.remove("--split")
        print("[INFO] Split Scan Mode: ON")

    if len(sys.argv) > 1:
        # 特定ファイル名が指定された場合
        target_files = []
        for arg in sys.argv[1:]:
            path = os.path.join(INPUT_DIR, arg)
            if os.path.exists(path):
                target_files.append(path)
            else:
                print(f"[WARN] ファイルが見つかりません: {path}")
                print(f"       input/images/ に画像を配置してください")
    else:
        # 全件処理
        target_files = sorted([
            os.path.join(INPUT_DIR, f)
            for f in os.listdir(INPUT_DIR)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        ])

    if not target_files:
        print("=" * 60)
        print("  処理対象の画像がありません")
        print(f"  画像を {INPUT_DIR}/ に配置してください")
        print("=" * 60)
        return

    print(f"\n{'='*60}")
    print(f"  実画像バッチテスト: {len(target_files)} 件")
    print(f"{'='*60}\n")

    results = []
    for path in target_files:
        summary = process_single_image(path, use_split=use_split)
        results.append(summary)
        print()  # 画像間のスペース

    # 全体サマリー
    print(f"\n{'='*60}")
    print(f"  全体結果サマリー")
    print(f"{'='*60}")
    for r in results:
        status = r.get("status", "")
        if status == "ERROR":
            print(f"  ❌ {r['file']}: ERROR - {r['error']}")
        elif status == "NO_RECEIPTS":
            print(f"  ⚠️  {r['file']}: レシート検出なし")
        else:
            v = r.get("valid_count", 0)
            inv = r.get("invalid_count", 0)
            mark = "✅" if v > 0 else "❌"
            print(f"  {mark} {r['file']}: {v} valid / {inv} invalid / {r['total_receipts']} total")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
