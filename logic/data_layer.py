"""
Data Abstraction Layer
- 環境変数 USE_CLOUD_BACKEND で動作を切り替え
- LOCAL: ファイルベース (summary.json)
- CLOUD: Turso + R2
"""
import os
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# Cloud backend flag
USE_CLOUD_BACKEND = os.getenv("USE_CLOUD_BACKEND", "false").lower() == "true"

# 遅延インポート: database / storage はモジュールレベルでインポートしない
# 関数呼び出し時に初めてインポートすることで、インポート連鎖エラーを防止
_db = None
_storage = None

def _get_db():
    """database モジュールを遅延インポート"""
    global _db
    if _db is None:
        from logic import database as _db_mod
        _db = _db_mod
    return _db

def _get_storage():
    """storage モジュールを遅延インポート"""
    global _storage
    if _storage is None:
        from logic import storage as _storage_mod
        _storage = _storage_mod
    return _storage


# ─────────────────────────────────────────────
# Type definitions (for compatibility)
# ─────────────────────────────────────────────
class CloudReceipt:
    """クラウド用のレシートデータ型"""
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", str(uuid.uuid4()))
        self.payee = kwargs.get("payee", "")  # vendor
        self.total_amount = kwargs.get("total_amount", 0)
        self.payment_date = kwargs.get("payment_date", "")  # date
        self.tax_rate = kwargs.get("tax_rate", "unknown")
        self.category = kwargs.get("category", "unknown")
        self.payment_method = kwargs.get("payment_method", "unknown")
        self.invoice_number = kwargs.get("invoice_number", "")
        self.invoice_candidates = kwargs.get("invoice_candidates", [])
        self.image_url = kwargs.get("image_url", "")  # R2のオブジェクトキー
        self.image_path = kwargs.get("image_path", "")  # 表示用URL
        self.status = kwargs.get("status", "valid")
        self.is_confirmed = kwargs.get("is_confirmed", False)
        self.is_discarded = kwargs.get("is_discarded", False)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payee": self.payee,
            "total_amount": self.total_amount,
            "payment_date": self.payment_date,
            "tax_rate": self.tax_rate,
            "category": self.category,
            "payment_method": self.payment_method,
            "invoice_number": self.invoice_number,
            "invoice_candidates": self.invoice_candidates,
            "image_url": self.image_url,
            "image_path": self.image_path,
            "status": self.status,
            "is_confirmed": self.is_confirmed,
            "is_discarded": self.is_discarded,
        }


# ─────────────────────────────────────────────
# Session Operations
# ─────────────────────────────────────────────
def create_session(name: Optional[str] = None) -> str:
    """新しいセッションを作成"""
    if USE_CLOUD_BACKEND:
        return _get_db().create_session(name)
    else:
        # Local: フォルダ作成
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = Path("output") / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_id


def list_sessions() -> list[dict]:
    """セッション一覧を取得"""
    if USE_CLOUD_BACKEND:
        return _get_db().list_sessions()
    else:
        # Local: 既存の _find_sessions と同等
        import json
        sessions = []
        base_dir = Path("output")
        if not base_dir.exists():
            return []
        
        all_dirs = sorted(list(base_dir.glob("*")), reverse=True)
        for d in all_dirs:
            if not d.is_dir():
                continue
            summary_path = d / "summary.json"
            if not summary_path.exists():
                continue
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append({
                    "id": d.name,
                    "name": d.name,
                    "created_at": data.get("timestamp", ""),
                    "path": str(summary_path),
                })
            except Exception:
                pass
        return sessions


def delete_session(session_id: str):
    """セッションを削除"""
    if USE_CLOUD_BACKEND:
        _get_db().delete_session(session_id)
    else:
        import shutil
        session_dir = Path("output") / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)


# ─────────────────────────────────────────────
# Receipt Operations
# ─────────────────────────────────────────────
def save_receipt(session_id: str, receipt: dict, image_data: Optional[bytes] = None, filename: str = "") -> str:
    """レシートを保存"""
    if USE_CLOUD_BACKEND:
        # 画像をR2にアップロード
        if image_data:
            object_key = _get_storage().upload_image_bytes(image_data, filename)
            receipt["image_url"] = object_key
            # 署名付きURLを取得して表示用に保存
            receipt["image_path"] = _get_storage().get_presigned_url(object_key)
        
        return _get_db().save_receipt(session_id, receipt)
    else:
        # Local: summary.json に追加
        raise NotImplementedError("Local save_receipt not implemented in this layer")


def get_receipts(session_id: str) -> list[dict]:
    """セッションのレシートを取得"""
    if USE_CLOUD_BACKEND:
        receipts = _get_db().get_receipts_by_session(session_id)
        # 署名付きURLを更新（期限切れ対策）
        for r in receipts:
            if r.get("image_url"):
                r["image_path"] = _get_storage().get_presigned_url(r["image_url"])
        return receipts
    else:
        raise NotImplementedError("Use _load_records for local mode")


def update_receipt(receipt_id: str, updates: dict):
    """レシートを更新"""
    if USE_CLOUD_BACKEND:
        _get_db().update_receipt(receipt_id, updates)
    else:
        raise NotImplementedError("Use _save_records for local mode")


def soft_delete_receipt(receipt_id: str):
    """レシートをソフト削除"""
    if USE_CLOUD_BACKEND:
        _get_db().soft_delete_receipt(receipt_id)
    else:
        raise NotImplementedError("Use _save_records for local mode")


def restore_receipt(receipt_id: str):
    """レシートを復元"""
    if USE_CLOUD_BACKEND:
        _get_db().restore_receipt(receipt_id)
    else:
        raise NotImplementedError("Use _save_records for local mode")


def get_trashed_receipts(session_id: str) -> list[dict]:
    """ゴミ箱のレシートを取得"""
    if USE_CLOUD_BACKEND:
        return _get_db().get_trashed_receipts(session_id)
    else:
        raise NotImplementedError("Use _load_records for local mode")


# ─────────────────────────────────────────────
# Image Operations
# ─────────────────────────────────────────────
def upload_image(image_data: bytes, filename: str) -> tuple[str, str]:
    """
    画像をアップロードし、(object_key, display_url) を返す
    """
    if USE_CLOUD_BACKEND:
        object_key = _get_storage().upload_image_bytes(image_data, filename)
        display_url = _get_storage().get_presigned_url(object_key)
        return object_key, display_url
    else:
        raise NotImplementedError("Local image upload not implemented in this layer")


def get_image_url(object_key: str) -> str:
    """画像の表示用URLを取得"""
    if USE_CLOUD_BACKEND:
        return _get_storage().get_presigned_url(object_key)
    else:
        # Local: ファイルパスをそのまま返す
        return object_key
