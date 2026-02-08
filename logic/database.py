"""
Turso Database Module (HTTP REST API version)
- requests を使用した Turso HTTP API への接続
- WebSocket不要、シンプルで安定
"""
import os
import uuid
from datetime import datetime
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

# Streamlit Cloud対応: st.secretsとos.getenvの両方をサポート
def _get_secret(key: str, default: str = "") -> str:
    """Streamlit Cloud (st.secrets) またはローカル (os.getenv) から値を取得"""
    # まずos.getenvを試す
    value = os.getenv(key, "")
    if value:
        return value
    
    # Streamlit Cloudの場合はst.secretsを試す
    try:
        import streamlit as st
        if hasattr(st, 'secrets') and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    
    return default

# Turso接続設定
TURSO_DATABASE_URL = _get_secret("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _get_secret("TURSO_AUTH_TOKEN")

# libsql:// を https:// に変換
if TURSO_DATABASE_URL.startswith("libsql://"):
    TURSO_HTTP_URL = TURSO_DATABASE_URL.replace("libsql://", "https://")
else:
    TURSO_HTTP_URL = TURSO_DATABASE_URL


def execute_sql(sql: str, args: list = None) -> dict:
    """
    Turso HTTP API でSQLを実行
    
    Args:
        sql: SQL文
        args: パラメータ（?プレースホルダ用）
    
    Returns:
        {"columns": [...], "rows": [...]}
    """
    if not TURSO_HTTP_URL or not TURSO_AUTH_TOKEN:
        raise ValueError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set in .env")
    
    url = f"{TURSO_HTTP_URL}/v2/pipeline"
    headers = {
        "Authorization": f"Bearer {TURSO_AUTH_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # パラメータをTurso形式に変換
    params = []
    if args:
        for arg in args:
            if arg is None:
                params.append({"type": "null"})
            elif isinstance(arg, int):
                params.append({"type": "integer", "value": str(arg)})
            elif isinstance(arg, float):
                params.append({"type": "float", "value": arg})
            elif isinstance(arg, str):
                params.append({"type": "text", "value": arg})
            else:
                params.append({"type": "text", "value": str(arg)})
    
    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": params}},
            {"type": "close"}
        ]
    }
    
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    
    result = response.json()
    
    # レスポンスからデータを抽出
    if "results" in result and len(result["results"]) > 0:
        first_result = result["results"][0]
        if "response" in first_result and "result" in first_result["response"]:
            res = first_result["response"]["result"]
            columns = [col["name"] for col in res.get("cols", [])]
            rows = []
            for row in res.get("rows", []):
                row_data = []
                for cell in row:
                    if cell.get("type") == "null":
                        row_data.append(None)
                    else:
                        row_data.append(cell.get("value"))
                rows.append(row_data)
            return {"columns": columns, "rows": rows}
    
    return {"columns": [], "rows": []}


# ─────────────────────────────────────────────
# Session CRUD
# ─────────────────────────────────────────────
def create_session(name: Optional[str] = None) -> str:
    """新しいセッションを作成し、IDを返す"""
    session_id = str(uuid.uuid4())
    if not name:
        name = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    execute_sql(
        "INSERT INTO sessions (id, name) VALUES (?, ?)",
        [session_id, name]
    )
    return session_id


def list_sessions() -> list[dict]:
    """全セッションをリストで返す"""
    result = execute_sql(
        "SELECT id, name, created_at FROM sessions ORDER BY created_at DESC"
    )
    return [{"id": row[0], "name": row[1], "created_at": row[2]} for row in result["rows"]]


def delete_session(session_id: str):
    """セッションと関連レシートを削除"""
    execute_sql("DELETE FROM receipts WHERE session_id = ?", [session_id])
    execute_sql("DELETE FROM sessions WHERE id = ?", [session_id])


# ─────────────────────────────────────────────
# Receipt CRUD
# ─────────────────────────────────────────────
def save_receipt(session_id: str, receipt: dict) -> str:
    """レシートを保存し、IDを返す"""
    receipt_id = receipt.get("id") or str(uuid.uuid4())
    
    execute_sql("""
        INSERT OR REPLACE INTO receipts 
        (id, session_id, payee, total_amount, payment_date, tax_rate, category, 
         payment_method, invoice_number, invoice_candidates, image_url, image_path,
         status, is_confirmed, is_discarded, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        receipt_id,
        session_id,
        receipt.get("payee", ""),
        receipt.get("total_amount", 0),
        receipt.get("payment_date", ""),
        receipt.get("tax_rate", ""),
        receipt.get("category", ""),
        receipt.get("payment_method", ""),
        receipt.get("invoice_number", ""),
        ",".join(receipt.get("invoice_candidates", [])) if receipt.get("invoice_candidates") else "",
        receipt.get("image_url", ""),
        receipt.get("image_path", ""),
        receipt.get("status", "valid"),
        1 if receipt.get("is_confirmed") else 0,
        1 if receipt.get("is_discarded") else 0,
        datetime.now().isoformat()
    ])
    return receipt_id


def get_receipts_by_session(session_id: str) -> list[dict]:
    """セッションに属するレシートを取得"""
    result = execute_sql("""
        SELECT id, payee, total_amount, payment_date, tax_rate, category,
               payment_method, invoice_number, invoice_candidates, image_url, image_path,
               status, is_confirmed, is_discarded, created_at, updated_at
        FROM receipts 
        WHERE session_id = ? AND is_discarded = 0
        ORDER BY created_at
    """, [session_id])
    
    receipts = []
    for row in result["rows"]:
        receipts.append({
            "id": row[0],
            "payee": row[1],
            "total_amount": row[2],
            "payment_date": row[3],
            "tax_rate": row[4],
            "category": row[5],
            "payment_method": row[6],
            "invoice_number": row[7],
            "invoice_candidates": row[8].split(",") if row[8] else [],
            "image_url": row[9],
            "image_path": row[10],
            "status": row[11],
            "is_confirmed": bool(row[12]),
            "is_discarded": bool(row[13]),
        })
    return receipts


def update_receipt(receipt_id: str, updates: dict):
    """レシートを部分更新"""
    set_clauses = []
    values = []
    
    field_mapping = {
        "payee": "payee",
        "total_amount": "total_amount",
        "payment_date": "payment_date",
        "tax_rate": "tax_rate",
        "category": "category",
        "payment_method": "payment_method",
        "invoice_number": "invoice_number",
        "image_url": "image_url",
        "image_path": "image_path",
        "status": "status",
        "is_confirmed": "is_confirmed",
        "is_discarded": "is_discarded",
    }
    
    for key, column in field_mapping.items():
        if key in updates:
            value = updates[key]
            if key in ("is_confirmed", "is_discarded"):
                value = 1 if value else 0
            set_clauses.append(f"{column} = ?")
            values.append(value)
    
    if not set_clauses:
        return
    
    set_clauses.append("updated_at = ?")
    values.append(datetime.now().isoformat())
    values.append(receipt_id)
    
    sql = f"UPDATE receipts SET {', '.join(set_clauses)} WHERE id = ?"
    execute_sql(sql, values)


def soft_delete_receipt(receipt_id: str):
    """レシートをソフト削除（ゴミ箱へ）"""
    update_receipt(receipt_id, {"is_discarded": True})


def restore_receipt(receipt_id: str):
    """ゴミ箱からレシートを復元"""
    update_receipt(receipt_id, {"is_discarded": False})


def get_trashed_receipts(session_id: str) -> list[dict]:
    """ゴミ箱のレシートを取得"""
    result = execute_sql("""
        SELECT id, payee, total_amount, payment_date, image_url
        FROM receipts 
        WHERE session_id = ? AND is_discarded = 1
        ORDER BY updated_at DESC
    """, [session_id])
    
    return [{"id": row[0], "payee": row[1], "total_amount": row[2], 
             "payment_date": row[3], "image_url": row[4]} for row in result["rows"]]


# ─────────────────────────────────────────────
# Test Connection
# ─────────────────────────────────────────────
def test_connection() -> bool:
    """接続テスト"""
    try:
        result = execute_sql("SELECT 1 as test")
        return len(result["rows"]) > 0
    except Exception as e:
        print(f"Connection test failed: {e}")
        return False
