import pandas as pd
from typing import List, Dict, Optional
from .models import ReceiptRecord, Category, PaymentMethod, TaxRate

# かんたんクラウド会計 CSV Column Definitions (Provisional 38 columns)
# ユーザー指定の必須項目:
# 内部月, 借方勘定科目コード, 借方金額, 借方消費税区分, 借方税込/税抜区分, 借方税率コード, 貸方勘定科目コード, 貸方金額, 摘要

# 仮のヘッダー定義（38項目）- 実際には仕様書に合わせて調整が必要
CSV_HEADERS = [
    "日付", "借方勘定科目コード", "借方補助科目コード", "借方部門コード", "借方税区分コード", 
    "借方消費税額", "借方金額", "貸方勘定科目コード", "貸方補助科目コード", "貸方部門コード", 
    "貸方税区分コード", "貸方消費税額", "貸方金額", "摘要", 
    # 以下、予備項目等 (合計38になるようにダミーなどで埋める必要があるが、まずは主要項目にフォーカス)
    "仕訳区分", "内部月", "借方税率", "貸方税率", "インボイス", # ... etc
]

# マスタ設定: カテゴリ -> 借方勘定科目コード
CATEGORY_ACCOUNT_MAP = {
    Category.TRAVEL: "1001",        # 仮コード: 旅費交通費
    Category.PARKING: "1001",       # 仮: 旅費交通費（補助科目等で分ける運用も想定）
    Category.TOLL: "1001",          # 仮
    Category.MEETING: "1002",       # 仮: 会議費
    Category.ENTERTAINMENT: "1003", # 仮: 交際費
    Category.SUPPLIES: "1004",      # 仮: 消耗品費
    Category.DUES: "1005",          # 仮: 諸会費
    Category.OTHER: "1999",         # 仮: 雑費
    Category.UNKNOWN: ""            # 未設定
}

# マスタ設定: 支払方法 -> 貸方勘定科目コード
PAYMENT_ACCOUNT_MAP = {
    PaymentMethod.CASH: "111",      # 仮: 現金
    PaymentMethod.PAYPAY: "112",    # 仮: 預け金
    PaymentMethod.CREDIT: "201",    # 仮: 未払金
    PaymentMethod.UNKNOWN: ""       # 未設定
}

# 税設定 (固定値)
TAX_CLASS_PURCHASE = "2"    # コード[2] = 仕入
TAX_INC_EXC_INC = "1"      # コード[1] = 税込
TAX_RATE_MAP = {
    TaxRate.RATE_10: "4",       # 10%
    TaxRate.RATE_8: "3",        # 8%
    TaxRate.RATE_8_REDUCED: "5",# 8%軽減
    TaxRate.UNKNOWN: "0"        # 不明/対象外
}

def validate_mandatory_fields(row: Dict[str, str]) -> List[str]:
    """
    ユーザー要件に基づく必須項目チェック
    戻り値: 不足している項目名のリスト (空ならOK)
    """
    required_keys = [
        "内部月", # Date derived
        "借方勘定科目コード",
        "借方金額",
        "借方消費税区分", 
        "借方税込/税抜区分",
        "借方税率コード",
        "貸方勘定科目コード",
        "貸方金額",
        "摘要"
    ]
    
    missing = []
    for key in required_keys:
        val = row.get(key)
        if not val or str(val).strip() == "":
            missing.append(key)
    
    return missing

def convert_record_to_row(record: ReceiptRecord) -> Dict[str, str]:
    """
    ReceiptRecordをCSV行(Dict)に変換する。
    この段階ではバリデーションエラーがあっても変換自体は行う。
    """
    # 日付処理
    try:
        # YYYY/MM/DD -> YYYYMM 形式
        dt_parts = record.date.split("/")
        if len(dt_parts) == 3:
            internal_month = dt_parts[0] + dt_parts[1]  # 例: 202602
        else:
            internal_month = ""
    except:
        internal_month = ""

    # マッピング
    debit_code = CATEGORY_ACCOUNT_MAP.get(record.category, "")
    credit_code = PAYMENT_ACCOUNT_MAP.get(record.payment_method, "")
    
    tax_rate_code = TAX_RATE_MAP.get(record.tax_rate_detected, "0")
    
    # 摘要: vendor + subject + invoice
    summary = f"{record.vendor} {record.subject}".strip()
    if record.invoice_no_norm:
        summary += f" INVOICE:{record.invoice_no_norm}"
        
    row = {
        "日付": record.date,
        "内部月": internal_month,
        
        "借方勘定科目コード": debit_code,
        "借方金額": str(record.total_amount),
        "借方消費税区分": TAX_CLASS_PURCHASE, # [2] 仕入
        "借方税込/税抜区分": TAX_INC_EXC_INC, # [1] 税込
        "借方税率コード": tax_rate_code,
        
        "貸方勘定科目コード": credit_code,
        "貸方金額": str(record.total_amount),
        
        "摘要": summary,
        
        # その他固定値や空欄など
        "仕訳区分": "1", # 一般仕訳
    }
    return row

def generate_csv_data(records: List[ReceiptRecord]) -> Dict[str, List[Dict]]:
    """
    レコードリストを受け取り、
    1. valid_rows: CSV出力してOKな行のリスト
    2. invalid_rows: 必須項目欠落などで弾かれた行（エラー理由付き）のリスト
    を返す。
    """
    valid_rows = []
    invalid_rows = []
    
    for record in records:
        # ユーザー確認済みでないレコードはスキップするか、バリデーションで弾くか
        # 要件：「1つでも欠ける場合...CSV出力対象から除外」
        
        row = convert_record_to_row(record)
        missing = validate_mandatory_fields(row)
        
        if missing:
            # エラーあり
            record.missing_fields = missing # モデル側にも情報を戻す（表示用）
            invalid_info = row.copy()
            invalid_info["_error_reasons"] = missing
            invalid_info["_original_record"] = record
            invalid_rows.append(invalid_info)
        else:
            # 正常
            valid_rows.append(row)
            
    return {
        "valid": valid_rows,
        "invalid": invalid_rows
    }
