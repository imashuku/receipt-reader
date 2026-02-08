"""
Cloudflare R2 Storage Module
- S3互換APIを使用した画像のアップロード・ダウンロード
"""
import os
import uuid
from pathlib import Path
from typing import Optional
import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

# Streamlit Cloud対応: st.secretsとos.getenvの両方をサポート
def _get_secret(key: str, default: str = None) -> str:
    """Streamlit Cloud (st.secrets) またはローカル (os.getenv) から値を取得"""
    # まずos.getenvを試す（ローカル開発用）
    value = os.getenv(key)
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

# R2設定を取得する関数（遅延評価でStreamlit Cloud対応）
def _get_r2_config():
    """R2設定を取得（毎回呼び出し時に評価）"""
    account_id = _get_secret("R2_ACCOUNT_ID")
    access_key = _get_secret("R2_ACCESS_KEY_ID")
    secret_key = _get_secret("R2_SECRET_ACCESS_KEY")
    bucket_name = _get_secret("R2_BUCKET_NAME", "receipt-reader")
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com" if account_id else None
    return account_id, access_key, secret_key, bucket_name, endpoint

def get_bucket_name():
    """バケット名を取得（遅延評価）"""
    return _get_secret("R2_BUCKET_NAME", "receipt-reader")

# 後方互換性のため（実際は get_bucket_name() を使うこと）
R2_BUCKET_NAME = "receipt-reader"  # デフォルト値。実行時は get_bucket_name() で取得

def get_r2_client():
    """R2クライアントを取得"""
    account_id, access_key, secret_key, bucket_name, endpoint = _get_r2_config()
    
    if not all([account_id, access_key, secret_key]):
        raise ValueError("R2 credentials not set. Check st.secrets or environment variables.")
    
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto"
    )


def upload_image(file_path: str, object_key: Optional[str] = None) -> str:
    """
    画像をR2にアップロードし、オブジェクトキーを返す
    
    Args:
        file_path: ローカルファイルパス
        object_key: R2でのオブジェクトキー（省略時は自動生成）
    
    Returns:
        R2のオブジェクトキー（例: "images/abc123.jpg"）
    """
    client = get_r2_client()
    
    path = Path(file_path)
    if not object_key:
        # ユニークなキーを生成
        ext = path.suffix.lower()
        object_key = f"images/{uuid.uuid4().hex}{ext}"
    
    # Content-Type を推定
    content_type = "image/jpeg"
    if path.suffix.lower() in [".png"]:
        content_type = "image/png"
    elif path.suffix.lower() in [".heic", ".heif"]:
        content_type = "image/heic"
    
    with open(file_path, "rb") as f:
        client.put_object(
            Bucket=get_bucket_name(),
            Key=object_key,
            Body=f,
            ContentType=content_type
        )
    
    return object_key


def upload_image_bytes(data: bytes, filename: str) -> str:
    """
    バイトデータを直接R2にアップロード
    
    Args:
        data: 画像のバイトデータ
        filename: ファイル名（拡張子判定用）
    
    Returns:
        R2のオブジェクトキー
    """
    client = get_r2_client()
    
    ext = Path(filename).suffix.lower() or ".jpg"
    object_key = f"images/{uuid.uuid4().hex}{ext}"
    
    content_type = "image/jpeg"
    if ext in [".png"]:
        content_type = "image/png"
    elif ext in [".heic", ".heif"]:
        content_type = "image/heic"
    
    client.put_object(
        Bucket=get_bucket_name(),
        Key=object_key,
        Body=data,
        ContentType=content_type
    )
    
    return object_key


def get_presigned_url(object_key: str, expires_in: int = 3600) -> str:
    """
    署名付きURLを生成（画像表示用）
    
    Args:
        object_key: R2のオブジェクトキー
        expires_in: 有効期限（秒）、デフォルト1時間
    
    Returns:
        署名付きURL
    """
    client = get_r2_client()
    
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": get_bucket_name(), "Key": object_key},
        ExpiresIn=expires_in
    )
    
    return url


def download_image(object_key: str) -> bytes:
    """
    R2から画像をダウンロード
    
    Args:
        object_key: R2のオブジェクトキー
    
    Returns:
        画像のバイトデータ
    """
    client = get_r2_client()
    
    response = client.get_object(
        Bucket=get_bucket_name(),
        Key=object_key
    )
    
    return response["Body"].read()


def delete_image(object_key: str):
    """
    R2から画像を削除
    
    Args:
        object_key: R2のオブジェクトキー
    """
    client = get_r2_client()
    
    client.delete_object(
        Bucket=get_bucket_name(),
        Key=object_key
    )


def list_images(prefix: str = "images/") -> list[str]:
    """
    R2の画像一覧を取得
    
    Args:
        prefix: 検索プレフィックス
    
    Returns:
        オブジェクトキーのリスト
    """
    client = get_r2_client()
    
    response = client.list_objects_v2(
        Bucket=get_bucket_name(),
        Prefix=prefix
    )
    
    if "Contents" not in response:
        return []
    
    return [obj["Key"] for obj in response["Contents"]]
