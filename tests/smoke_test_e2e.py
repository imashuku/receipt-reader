"""
スモークテスト: E2Eパイプライン
画像 → Gemini OCR → ReceiptRecord → CSV出力（valid/invalidの分類込み）

使い方:
  python tests/smoke_test_e2e.py <レシート画像パス>
  python tests/smoke_test_e2e.py  # ダミー画像で実行
"""
import sys
import os
import csv

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.gemini_client import analyze_receipt_image
from logic.exporter import generate_csv_data, validate_mandatory_fields, convert_record_to_row
from logic.models import ReceiptRecord, Category, PaymentMethod, TaxRate

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")


def create_dummy_receipt_image() -> str:
    """テスト用のダミーレシート画像を生成"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (400, 600), "white")
    draw = ImageDraw.Draw(img)

    # シンプルなレシート風テキストを描画
    lines = [
        "━━━━━━━━━━━━━━━━",
        "   かえる代行サービス",
        "━━━━━━━━━━━━━━━━",
        "",
        " 日付: 2026/02/08",
        "",
        " ご利用料金",
        " 代行料金     ¥3,500",
        " 深夜割増       ¥500",
        " ────────────────",
        " 合計        ¥4,000",
        "  (税込10%)",
        "",
        " お預り      ¥5,000",
        " お釣り      ¥1,000",
        "",
        " T1234567890123",
        "",
        "  ありがとうございました",
        "━━━━━━━━━━━━━━━━",
    ]

    y = 30
    for line in lines:
        draw.text((20, y), line, fill="black")
        y += 25

    path = os.path.join(OUTPUT_DIR, "dummy_receipt.png")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    img.save(path)
    print(f"[INFO] ダミーレシート画像を生成: {path}")
    return path


def run_smoke_test(image_path: str):
    """E2Eスモークテスト本体"""
    print("=" * 60)
    print("  スモークテスト: レシート画像 → CSV出力")
    print("=" * 60)

    # ── Step 1: Gemini OCR ──
    print(f"\n[Step 1] Gemini OCR 実行中... ({image_path})")
    records = analyze_receipt_image(image_path)

    if not records:
        print("[FAIL] レシートが1件も抽出されませんでした。")
        return False

    print(f"[OK] {len(records)} 件のレシートを抽出")
    for i, rec in enumerate(records):
        print(f"  [{i}] {rec.date} | {rec.vendor} | ¥{rec.total_amount} | "
              f"税率={rec.tax_rate_detected.value} | 支払={rec.payment_method.value} | "
              f"T番号={rec.invoice_no_norm or '(なし)'} | "
              f"要確認={rec.needs_review}")

    # ── Step 1.5: テスト用の補完（本番ではUI側でユーザーが設定） ──
    # category / payment_method が UNKNOWN のままだと勘定科目コード空 → CSV除外される
    # ので、テスト用にデフォルト値を設定
    for rec in records:
        if rec.category == Category.UNKNOWN:
            rec.category = Category.OTHER  # 仮: 雑費
            print(f"  [補完] カテゴリ未設定 → 雑費(OTHER)に仮設定")
        if rec.payment_method == PaymentMethod.UNKNOWN:
            rec.payment_method = PaymentMethod.CASH  # 仮: 現金
            print(f"  [補完] 支払方法未設定 → 現金(CASH)に仮設定")

    # ── Step 2: CSV変換 & バリデーション ──
    print(f"\n[Step 2] CSV変換 & 必須項目チェック...")
    result = generate_csv_data(records)

    valid = result["valid"]
    invalid = result["invalid"]

    print(f"  Valid:   {len(valid)} 件")
    print(f"  Invalid: {len(invalid)} 件")

    for inv in invalid:
        print(f"  [NG] 不足項目: {inv['_error_reasons']}")

    # ── Step 3: CSV書き出し ──
    if valid:
        csv_path = os.path.join(OUTPUT_DIR, "test_output.csv")
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        # ヘッダーを valid の最初の行のキーから取得（_internal用は除外）
        headers = [k for k in valid[0].keys() if not k.startswith("_")]

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in valid:
                writer.writerow({k: v for k, v in row.items() if not k.startswith("_")})

        print(f"\n[Step 3] CSV出力完了: {csv_path}")

        # 内容表示
        print("\n── CSV内容 ──")
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            print(f.read())
    else:
        print("\n[Step 3] 有効なレコードがないためCSV出力をスキップ")

    # ── 結果サマリー ──
    print("=" * 60)
    success = len(valid) > 0
    if success:
        print("  ✅ スモークテスト PASSED")
    else:
        print("  ❌ スモークテスト FAILED (有効レコード0件)")
    print("=" * 60)
    return success


def run_unit_test_exporter():
    """exporter単体テスト（Gemini不要、ローカル実行）"""
    print("\n── exporter単体テスト ──")

    # テスト1: 全項目あり → valid
    rec_ok = ReceiptRecord(
        date="2026/02/08",
        vendor="かえる代行",
        subject="代行料金",
        total_amount=4000,
        invoice_no_norm="T1234567890123",
        qualified_flag="○",
        tax_rate_detected=TaxRate.RATE_10,
        payment_method=PaymentMethod.CASH,
        category=Category.TRAVEL,
        needs_review=False,
    )
    row = convert_record_to_row(rec_ok)
    missing = validate_mandatory_fields(row)
    assert missing == [], f"[FAIL] 全項目ありなのに不足: {missing}"
    assert row["内部月"] == "202602", f"[FAIL] 内部月が不正: {row['内部月']}"
    assert row["借方消費税区分"] == "2", f"[FAIL] 消費税区分が不正: {row['借方消費税区分']}"
    print("  [OK] テスト1: 全項目あり → valid, 内部月=202602, 消費税区分=2")

    # テスト2: カテゴリ未設定 → 借方勘定科目コードが空 → invalid
    rec_ng = ReceiptRecord(
        date="2026/02/08",
        vendor="テスト店",
        total_amount=1000,
        tax_rate_detected=TaxRate.RATE_10,
        payment_method=PaymentMethod.CASH,
        # category は デフォルトで UNKNOWN → コード空文字
    )
    row2 = convert_record_to_row(rec_ng)
    missing2 = validate_mandatory_fields(row2)
    assert "借方勘定科目コード" in missing2, f"[FAIL] カテゴリ未設定なのに通過: {missing2}"
    print("  [OK] テスト2: カテゴリ未設定 → invalid (借方勘定科目コード欠落)")

    # テスト3: 日付空 → 内部月欠落 → invalid
    rec_no_date = ReceiptRecord(
        date="",
        vendor="テスト店",
        total_amount=500,
        category=Category.SUPPLIES,
        payment_method=PaymentMethod.PAYPAY,
        tax_rate_detected=TaxRate.RATE_10,
    )
    row3 = convert_record_to_row(rec_no_date)
    missing3 = validate_mandatory_fields(row3)
    assert "内部月" in missing3, f"[FAIL] 日付空なのに内部月が通過: {missing3}"
    print("  [OK] テスト3: 日付空 → invalid (内部月欠落)")

    print("  ✅ exporter単体テスト ALL PASSED\n")


if __name__ == "__main__":
    # まず exporter 単体テスト（ネットワーク不要）
    run_unit_test_exporter()

    # E2Eスモークテスト
    if len(sys.argv) > 1:
        img_path = sys.argv[1]
    else:
        img_path = create_dummy_receipt_image()

    run_smoke_test(img_path)
