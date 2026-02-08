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

# R2接続設定
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "receipt-reader")

# R2エンドポイント
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

def get_r2_client():
    """R2クライアントを取得"""
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY]):
        raise ValueError("R2 credentials not set in environment variables")
    
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
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
            Bucket=R2_BUCKET_NAME,
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
        Bucket=R2_BUCKET_NAME,
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
        Params={"Bucket": R2_BUCKET_NAME, "Key": object_key},
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
        Bucket=R2_BUCKET_NAME,
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
        Bucket=R2_BUCKET_NAME,
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
        Bucket=R2_BUCKET_NAME,
        Prefix=prefix
    )
    
    if "Contents" not in response:
        return []
    
    return [obj["Key"] for obj in response["Contents"]]
