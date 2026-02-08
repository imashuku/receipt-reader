import os
import json
import uuid
from pathlib import Path
from datetime import datetime
import streamlit as st

# logic/models.pyなどがapp.pyのsys.path設定により参照可能であることを前提
# 相対インポートではなく絶対インポートを使用
try:
    from logic.models import ReceiptRecord, TaxRate, PaymentMethod, Category
    from logic import data_layer
except ImportError:
    # app.pyからロードされた場合、logicがsys.modulesにあるはず
    import sys
    if "logic.models" in sys.modules:
        ReceiptRecord = sys.modules["logic.models"].ReceiptRecord
        TaxRate = sys.modules["logic.models"].TaxRate
        PaymentMethod = sys.modules["logic.models"].PaymentMethod
        Category = sys.modules["logic.models"].Category
    if "logic.data_layer" in sys.modules:
        data_layer = sys.modules["logic.data_layer"]

# 定数
BASE_OUTPUT_DIR = Path("output")
INPUT_DIR = Path("input/inbox")
DONE_DIR = Path("input/done")
FAILED_DIR = Path("input/failed")

def get_current_session_dir():
    if "current_session_dir" in st.session_state:
        return Path(st.session_state.current_session_dir)
    return None

def find_sessions(use_cloud: bool) -> list[dict]:
    """output/ 配下の summary.json を探索し、セッション一覧を返す（クラウドモード対応）"""
    if use_cloud:
        # クラウドモード: Turso DBからセッション一覧を取得
        # data_layerは遅延インポートされている可能性があるため、ここで取得トライ
        if 'logic.data_layer' not in locals():
             import sys
             if "logic.data_layer" in sys.modules:
                dl = sys.modules["logic.data_layer"]
             else:
                # Fallback: app.py経由でなければ使えない可能性があるが、
                # app.pyで初期化済みであることを期待
                from logic import data_layer as dl
        else:
             dl = data_layer

        db_sessions = dl.list_sessions()
        sessions = []
        for s in db_sessions:
            sessions.append({
                "dir": s.get("id", ""),
                "file": "",
                "total": 0,  # TODO: レシート数を取得
                "valid": 0,
                "invalid": 0,
                "path": s.get("id", ""),  # クラウドではセッションIDをパスとして使用
                "timestamp": s.get("created_at", ""),
                "is_cloud": True,
            })
        return sessions
    
    # ローカルモード: ファイルベース
    sessions = []
    # glob して、フォルダ名でソート(降順)
    if not BASE_OUTPUT_DIR.exists():
        BASE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
    all_dirs = sorted(list(BASE_OUTPUT_DIR.glob("*")), reverse=True)
    
    for d in all_dirs:
        if not d.is_dir(): continue
        summary_path = d / "summary.json"
        if not summary_path.exists(): continue
        
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "dir": d.name,
                "file": data.get("file", ""),
                "total": data.get("total_receipts", 0),
                "valid": data.get("valid_count", 0),
                "invalid": data.get("invalid_count", 0),
                "path": str(summary_path),
                "timestamp": data.get("timestamp", ""),
                "is_cloud": False,
            })
        except Exception:
            pass
    return sessions


def load_records(summary_path: str, use_cloud: bool) -> tuple[list, dict]:
    """summary.json からレコードリストを読み込む（クラウドモード対応）"""
    
    if use_cloud:
        import sys
        if "logic.data_layer" in sys.modules:
            dl = sys.modules["logic.data_layer"]
        else:
            from logic import data_layer as dl

        # クラウドモード: summary_pathはセッションID
        session_id = summary_path
        db_receipts = dl.get_receipts(session_id)
        
        records = []
        for r in db_receipts:
            rec = ReceiptRecord(
                date=r.get("payment_date", ""),
                vendor=r.get("payee", ""),
                subject="",
                total_amount=r.get("total_amount", 0),
                invoice_no_norm=r.get("invoice_number", ""),
                invoice_candidate=",".join(r.get("invoice_candidates", [])),
                qualified_flag="○" if r.get("invoice_number", "") else "",
                tax_rate_detected=TaxRate(r.get("tax_rate", "unknown")),
                payment_method=PaymentMethod(r.get("payment_method", "unknown")),
                category=Category(r.get("category", "unknown")),
                needs_review=r.get("status", "valid") == "needs_review",
                missing_fields=[],
                region=None,
                merge_candidates=[],
                merge_reason="",
                group_id="",
                is_confirmed=r.get("is_confirmed", False),
                backend_used="cloud",
                is_discarded=r.get("is_discarded", False),
                image_path=r.get("image_path", ""),  # 署名付きURL
            )
            # クラウド用ID保存
            rec._cloud_id = r.get("id", "")
            records.append(rec)
        
        # ダミーデータ（クラウドモードでは使わない）
        data = {"session_id": session_id, "records": [], "is_cloud": True}
        return records, data
    
    # ローカルモード: ファイルベース
    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = []
    for r in data.get("records", []):
        rec = ReceiptRecord(
            date=r.get("date", ""),
            vendor=r.get("vendor", ""),
            subject=r.get("subject", ""),
            total_amount=r.get("total_amount", 0),
            invoice_no_norm=r.get("invoice_no", ""),
            invoice_candidate=r.get("invoice_candidate", ""),
            qualified_flag="○" if r.get("invoice_no", "") else "",
            tax_rate_detected=TaxRate(r.get("tax_rate", "unknown")),
            payment_method=PaymentMethod(r.get("payment_method", "unknown")),
            category=Category(r.get("category", "unknown")),
            needs_review=r.get("needs_review", True),
            missing_fields=r.get("missing_fields", []),
            region=r.get("region", None),
            # Merge Info
            merge_candidates=r.get("merge_candidates", []),
            merge_reason=r.get("merge_reason", ""),
            group_id=r.get("group_id", ""),
            # Confirm
            is_confirmed=r.get("is_confirmed", False),
            
            # Backend
            backend_used=r.get("backend_used", ""),
            
            # Phase 10: Soft Delete
            is_discarded=r.get("is_discarded", False),
            
            # Image Path
            image_path=r.get("image_path", ""),
        )
        records.append(rec)
    return records, data


def save_records(summary_path: str, records: list, original_data: dict, use_cloud: bool):
    """レコードリストを summary.json に書き戻す（クラウドモード対応）"""
    
    if use_cloud:
        import sys
        if "logic.data_layer" in sys.modules:
            dl = sys.modules["logic.data_layer"]
        else:
            from logic import data_layer as dl

        # クラウドモード: summary_pathはセッションID
        session_id = summary_path if isinstance(summary_path, str) and not summary_path.endswith(".json") else original_data.get("session_id", "")
        
        for rec in records:
            receipt_data = {
                "payee": rec.vendor,
                "total_amount": rec.total_amount,
                "payment_date": rec.date,
                "tax_rate": rec.tax_rate_detected.value,
                "category": rec.category.value,
                "payment_method": rec.payment_method.value,
                "invoice_number": rec.invoice_no_norm,
                "invoice_candidates": rec.invoice_candidate.split(",") if rec.invoice_candidate else [],
                "image_path": rec.image_path,
                "status": "needs_review" if rec.needs_review else "valid",
                "is_confirmed": rec.is_confirmed,
                "is_discarded": rec.is_discarded,
            }
            
            # 既存レコードの更新 or 新規作成
            if hasattr(rec, "_cloud_id") and rec._cloud_id:
                receipt_data["id"] = rec._cloud_id
                dl.update_receipt(rec._cloud_id, receipt_data)
            else:
                dl.save_receipt(session_id, receipt_data)
        return
    
    # ローカルモード: ファイルベース
    serialized = []
    valid_count = 0
    invalid_count = 0
    for rec in records:
        entry = {
            "date": rec.date,
            "vendor": rec.vendor,
            "subject": rec.subject,
            "total_amount": rec.total_amount,
            "tax_rate": rec.tax_rate_detected.value,
            "payment_method": rec.payment_method.value,
            "invoice_no": rec.invoice_no_norm,
            "invoice_candidate": rec.invoice_candidate,
            "category": rec.category.value,
            "needs_review": rec.needs_review,
            "missing_fields": rec.missing_fields,
            "region": rec.region,
            "merge_candidates": rec.merge_candidates,
            "merge_reason": rec.merge_reason,
            "group_id": rec.group_id,
            "is_confirmed": rec.is_confirmed,
            "backend_used": rec.backend_used,
            "is_discarded": rec.is_discarded,
            "image_path": rec.image_path,
        }
        serialized.append(entry)
        
        # Valid カウント
        if not rec.is_discarded:
             if not rec.missing_fields and not rec.needs_review: #Simplified logic
                  pass

        if not rec.is_discarded and not rec.missing_fields:
            valid_count += 1
        else:
            invalid_count += 1

    original_data["records"] = serialized
    original_data["valid_count"] = valid_count
    original_data["invalid_count"] = invalid_count

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(original_data, f, ensure_ascii=False, indent=2)
