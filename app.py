"""
レシート編集UI (Streamlit)
- summary.json を読み書きしてレシートデータを編集
- valid/invalid の再判定、CSV出力
"""
# ─────────────────────────────────────────────
# Step 1: 標準ライブラリ + Streamlit
# ─────────────────────────────────────────────
import streamlit as st
import streamlit.components.v1 as components
import json
import os
import sys
import base64
import glob
import uuid
import pandas as pd
from pathlib import Path
from datetime import datetime
import subprocess
import socket
import shutil
from dotenv import load_dotenv

# Streamlit Cloud対応: sys.pathにプロジェクトルートを追加
_project_root = str(Path(__file__).resolve().parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ─────────────────────────────────────────────
# Step 2: 環境変数を最初にセットアップ
#   load_dotenv() → st.secrets転写 → 全os.getenv()が使える状態にする
# ─────────────────────────────────────────────
load_dotenv()

# Streamlit Cloud対応: st.secretsの値をos.environに転写
# logic配下のモジュールがos.getenv()で読むため、インポート前にセットする
try:
    for key in st.secrets:
        if isinstance(st.secrets[key], str) and key not in os.environ:
            os.environ[key] = st.secrets[key]
except Exception:
    pass  # ローカル実行時はst.secretsがないのでスキップ

USE_CLOUD_BACKEND = os.environ.get("USE_CLOUD_BACKEND", "false").lower() == "true"

# ─────────────────────────────────────────────
# Step 3: logicモジュールのインポート（環境変数セットアップ済み）
#   Streamlit Cloudではst.rerun()時にPythonのインポートキャッシュが壊れ、
#   KeyError: 'logic.xxx' が発生するため、毎回キャッシュをリセットする
# ─────────────────────────────────────────────
import importlib
importlib.invalidate_caches()
for _k in list(sys.modules.keys()):
    if _k.startswith("logic"):
        del sys.modules[_k]

try:
    from logic.models import ReceiptRecord, TaxRate, PaymentMethod, Category
    from logic.exporter import generate_csv_data, revalidate_record
    from logic.gemini_client import analyze_receipt_image, rescan_specific_area
except Exception as _import_err:
    st.error(f"❌ コアモジュール読み込みエラー: {_import_err}")
    import traceback
    st.code(traceback.format_exc(), language="text")
    st.stop()

# Step 4: クラウドバックエンドモジュール（オプション）
if USE_CLOUD_BACKEND:
    try:
        from logic import data_layer
        from logic.storage import upload_image_bytes, get_presigned_url
    except Exception as _cloud_err:
        st.warning(f"⚠️ クラウドバックエンド初期化失敗（ローカルモードで動作）: {_cloud_err}")
        USE_CLOUD_BACKEND = False

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
BASE_OUTPUT_DIR = Path("output")
INPUT_DIR = Path("input/inbox")  # Phase 11: Changed to inbox
DONE_DIR = Path("input/done")
FAILED_DIR = Path("input/failed")

def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _convert_heic_to_jpg(input_path: Path) -> Path:
    """
    HEIC/HEIFをJPEGに変換する (macOS sips利用)。
    変換成功なら新しいパスを返す。失敗なら元のパスを返す(または例外)。
    """
    if input_path.suffix.lower() not in {".heic", ".heif"}:
        return input_path
        
    out_path = input_path.with_suffix(".jpg")
    try:
        # sips -s format jpeg input --out output
        subprocess.run(
            ["sips", "-s", "format", "jpeg", str(input_path), "--out", str(out_path)],
            check=True,
            capture_output=True
        )
        if out_path.exists():
            input_path.unlink() # 元ファイルを削除
            return out_path
    except Exception as e:
        print(f"HEIC conversion failed: {e}")
    
    return input_path

def _get_current_session_dir():
    if "current_session_dir" in st.session_state:
        return Path(st.session_state.current_session_dir)
    return None

def _find_sessions() -> list[dict]:
    """output/ 配下の summary.json を探索し、セッション一覧を返す（クラウドモード対応）"""
    if USE_CLOUD_BACKEND:
        # クラウドモード: Turso DBからセッション一覧を取得
        db_sessions = data_layer.list_sessions()
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
    # タイムスタンプ順 (新しい順) にソートしたいが、フォルダ名がタイムスタンプとは限らない (以前の legacy フォルダなど)
    # glob して、フォルダ名でソート(降順)
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


def _load_records(summary_path: str) -> tuple[list[ReceiptRecord], dict]:
    """summary.json からレコードリストを読み込む（クラウドモード対応）"""
    
    if USE_CLOUD_BACKEND:
        # クラウドモード: summary_pathはセッションID
        session_id = summary_path
        db_receipts = data_layer.get_receipts(session_id)
        
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
            
            # Image Path (Added to fix missing image issue)
            image_path=r.get("image_path", ""),
        )
        records.append(rec)
    return records, data


def _save_records(summary_path: str, records: list[ReceiptRecord], original_data: dict):
    """レコードリストを summary.json に書き戻す（クラウドモード対応）"""
    
    if USE_CLOUD_BACKEND:
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
                data_layer.update_receipt(rec._cloud_id, receipt_data)
            else:
                data_layer.save_receipt(session_id, receipt_data)
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
            "missing_fields": rec.missing_fields,
            "region": rec.region,
            "merge_candidates": rec.merge_candidates,
            "merge_reason": rec.merge_reason,
            "group_id": rec.group_id,
            "group_id": rec.group_id,
            "is_confirmed": rec.is_confirmed,
            "backend_used": rec.backend_used,
            "is_discarded": rec.is_discarded,
            "image_path": rec.image_path,
        }
        serialized.append(entry)
        
        # Valid カウント (Phase 10: Discardedは除外)
        if not rec.is_discarded:
            if not rec.missing_fields and rec.invoice_no_norm:
                pass # Logic complex, simplify: existing logic
        
        # 簡易カウント (詳細判定は exporter 側だが、ここでは目安)
        if not rec.is_discarded and not rec.missing_fields:
            valid_count += 1
        else:
            invalid_count += 1

    original_data["records"] = serialized
    original_data["valid_count"] = valid_count
    original_data["invalid_count"] = invalid_count

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(original_data, f, ensure_ascii=False, indent=2)


def _get_status(rec: ReceiptRecord) -> str:
    """ステータスラベルを返す"""
    if not rec.missing_fields and not rec.needs_review:
        return "valid"
    elif rec.needs_review:
        return "needs_review"
    else:
        return "invalid"


def _status_emoji(status: str) -> str:
    return {"valid": "✅", "needs_review": "⚠️", "invalid": "❌"}.get(status, "❓")


def _render_zoomable_image(img_path: str):
    """
    パン＆ズーム画像ビューア。
    - ホイール: ズームイン/アウト（カーソル位置を中心に）
    - ドラッグ: パン（拡大中に画像を移動）
    - ダブルクリック: リセット（全体表示に戻す）
    - 拡大状態はマウスを離しても維持される
    - クラウドURL（https://）にも対応
    """
    from PIL import Image as PILImage
    import io
    
    # URLの場合はrequestsで取得、ローカルファイルの場合は直接読み込み
    if img_path.startswith("http://") or img_path.startswith("https://"):
        import requests
        try:
            response = requests.get(img_path, timeout=10)
            response.raise_for_status()
            img_data = response.content
            img_b64 = base64.b64encode(img_data).decode()
            
            # MIMEタイプをContent-Typeから取得
            content_type = response.headers.get("Content-Type", "image/png")
            mime = content_type.split(";")[0].strip()
            
            # 画像サイズ取得
            with PILImage.open(io.BytesIO(img_data)) as pil_img:
                w, h = pil_img.size
                display_h = min(int(600 * h / w), 760)
        except Exception as e:
            st.error(f"画像の読み込みに失敗しました: {e}")
            return
    else:
        # ローカルファイル
        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        ext = Path(img_path).suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp"}.get(ext, "image/png")
        
        try:
            with PILImage.open(img_path) as pil_img:
                w, h = pil_img.size
                display_h = min(int(600 * h / w), 760)
        except Exception:
            display_h = 650
    
    data_url = f"data:{mime};base64,{img_b64}"

    html = f"""
    <style>
      html, body {{ margin:0; padding:0; width:100%; height:100%; overflow:hidden; }}
      .pz-wrap {{
        position: relative; width: 100%; height: {display_h}px;
        overflow: hidden; border: 1px solid #ddd; border-radius: 8px;
        background: #f8f8f8;
      }}
      .pz-wrap img {{
        position: absolute; top: 0; left: 0;
        transform-origin: 0 0;
        will-change: transform;
        user-select: none; -webkit-user-drag: none;
      }}
      .pz-hud {{
        position: absolute; bottom: 8px; right: 8px;
        display: flex; gap: 6px; z-index: 20;
      }}
      .pz-hud button, .pz-hud .pz-label {{
        background: rgba(0,0,0,.65); color: #fff;
        font: bold 12px/1 sans-serif; border: none;
        padding: 5px 10px; border-radius: 4px; cursor: pointer;
      }}
      .pz-hud button:hover {{ background: rgba(0,0,0,.8); }}
      .pz-hud .pz-label {{ cursor: default; min-width: 48px; text-align: center; }}
    </style>
    <div class="pz-wrap" id="pzw">
      <img src="{data_url}" id="pzi" />
      <div class="pz-hud">
        <button id="pzm" title="ズームアウト">−</button>
        <div class="pz-label" id="pzl">100%</div>
        <button id="pzp" title="ズームイン">＋</button>
        <button id="pzr" title="リセット">↺</button>
      </div>
    </div>
    <script>
    (function(){{
      const wrap=document.getElementById('pzw'),
            img=document.getElementById('pzi'),
            lbl=document.getElementById('pzl');
      let sc=1, tx=0, ty=0, dragging=false, sx=0, sy=0, stx=0, sty=0;

      function apply(){{
        img.style.transform='translate('+tx+'px,'+ty+'px) scale('+sc+')';
        lbl.textContent=Math.round(sc*100)+'%';
      }}

      function fitImage(){{
        const ww=wrap.clientWidth, wh=wrap.clientHeight,
              iw=img.naturalWidth, ih=img.naturalHeight;
        if(!iw||!ih) return;
        const ratio=Math.min(ww/iw, wh/ih, 1);
        sc=ratio; tx=(ww-iw*sc)/2; ty=(wh-ih*sc)/2;
        apply();
      }}

      img.onload=fitImage;
      if(img.complete) fitImage();

      /* ホイールズーム（カーソル位置を中心に） */
      wrap.addEventListener('wheel',function(e){{
        e.preventDefault();
        const rect=wrap.getBoundingClientRect();
        const mx=e.clientX-rect.left, my=e.clientY-rect.top;
        const oldSc=sc;
        const factor=e.deltaY<0?1.15:1/1.15;
        sc=Math.max(0.2, Math.min(10, sc*factor));
        tx=mx-(mx-tx)*(sc/oldSc);
        ty=my-(my-ty)*(sc/oldSc);
        apply();
      }},{{passive:false}});

      /* ドラッグでパン */
      wrap.addEventListener('mousedown',function(e){{
        if(e.button!==0) return;
        dragging=true; sx=e.clientX; sy=e.clientY; stx=tx; sty=ty;
        wrap.style.cursor='grabbing';
      }});
      window.addEventListener('mousemove',function(e){{
        if(!dragging) return;
        tx=stx+(e.clientX-sx); ty=sty+(e.clientY-sy);
        apply();
      }});
      window.addEventListener('mouseup',function(){{
        dragging=false; wrap.style.cursor='grab';
      }});
      wrap.style.cursor='grab';

      /* ダブルクリックでリセット */
      wrap.addEventListener('dblclick',function(){{ fitImage(); }});

      /* ボタン操作 */
      document.getElementById('pzp').addEventListener('click',function(){{
        const cx=wrap.clientWidth/2, cy=wrap.clientHeight/2;
        const oldSc=sc; sc=Math.min(10,sc*1.3);
        tx=cx-(cx-tx)*(sc/oldSc); ty=cy-(cy-ty)*(sc/oldSc);
        apply();
      }});
      document.getElementById('pzm').addEventListener('click',function(){{
        const cx=wrap.clientWidth/2, cy=wrap.clientHeight/2;
        const oldSc=sc; sc=Math.max(0.2,sc/1.3);
        tx=cx-(cx-tx)*(sc/oldSc); ty=cy-(cy-ty)*(sc/oldSc);
        apply();
      }});
      document.getElementById('pzr').addEventListener('click',function(){{ fitImage(); }});
    }})();
    </script>
    """
    components.html(html, height=display_h + 20, scrolling=False)


def _render_merge_stats(records: list[ReceiptRecord]):
    """マージ統計情報を表示"""
    if not records:
        return

    # 1. 統計計算
    merged_count = len(records)
    # raw_count: マージ候補の合計 (候補がない場合は自分自身で1)
    raw_count = sum(len(r.merge_candidates) if r.merge_candidates else 1 for r in records)
    
    if raw_count == 0: raw_count = merged_count # avoid div0

    merge_ratio = (1 - (merged_count / raw_count)) * 100
    
    # グループサイズ計算
    group_sizes = [len(r.merge_candidates) if r.merge_candidates else 1 for r in records]
    max_group = max(group_sizes) if group_sizes else 1
    groups_gt_1 = sum(1 for s in group_sizes if s > 1)

    # 2. 表示
    with st.expander("📊 Merge Statistics", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Raw Records", f"{raw_count}")
        c2.metric("Merged Records", f"{merged_count}")
        c3.metric("Merge Ratio", f"{merge_ratio:.1f}%")
        c4.metric("Max Group Size", f"{max_group}", help=f"Number of groups > 1: {groups_gt_1}")

        # 3. マージ理由の内訳
        reasons = [r.merge_reason for r in records if r.merge_reason]
        if reasons:
            st.caption("Merge Reason Breakdown")
            reason_counts = pd.Series(reasons).value_counts().reset_index()
            reason_counts.columns = ["Reason", "Count"]
            st.dataframe(reason_counts, use_container_width=True, hide_index=True)



# ─────────────────────────────────────────────
# ページ設定
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# Mobile Mode Render Logic
# ─────────────────────────────────────────────
def render_mobile_mode():
    st.title("📱 レシート撮影・アップロード")
    
    # Connection Info
    ips = []
    try:
        # Get all interface IPs
        for info in socket.getaddrinfo(socket.gethostname(), None):
             ip = info[4][0]
             # Filter IPv4 private ranges (192.168.x.x, 10.x.x.x, 172.16.x.x)
             if "." in ip and not ip.startswith("127."):
                 ips.append(ip)
        ips = sorted(list(set(ips)))
    except:
        ips = [_get_local_ip()]

    if not ips:
         st.warning("有効なIPアドレスが見つかりませんでした。")
    else:
         with st.expander("📡 接続用QRコード・URLを表示", expanded=False):
             st.caption("以下のURLをiPhoneで開いてください (同一Wi-Fi必須):")
             for ip in ips:
                 st.code(f"http://{ip}:8501", language="text")
             st.caption("※繋がらない場合はポート番号 (:8502等) を確認してください")
             
    st.info("📸 iPhoneでレシートを撮影し、ここでアップロードしてください。\n解析はPCで行います。")
    
    # Upload Section
    with st.form("upload_form", clear_on_submit=True):
        uploaded_files = st.file_uploader(
            "レシートを選択 (複数まとめてアップロード可)",
            type=["png", "jpg", "jpeg", "heic", "heif"],
            accept_multiple_files=True
        )
        submitted = st.form_submit_button("📤 送信 (Inboxへ)", type="primary", use_container_width=True)
        
        if submitted and uploaded_files:
            count = 0
            for vid in uploaded_files:
                # Save
                file_bytes = vid.read()
                # Timestamp + UUID name
                ext = Path(vid.name).suffix.lower()
                
                # Timestamp for sortability
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                # UUID for uniqueness
                uid = str(uuid.uuid4())[:8]
                fname = f"{ts}_{uid}{ext}"
                
                if USE_CLOUD_BACKEND:
                    # クラウドモード: R2のinboxフォルダにアップロード
                    from logic.storage import upload_image_bytes as r2_upload
                    object_key = f"inbox/{fname}"
                    # R2に直接アップロード
                    from logic.storage import get_r2_client, get_bucket_name
                    client = get_r2_client()
                    content_type = "image/jpeg"
                    if ext in [".png"]:
                        content_type = "image/png"
                    elif ext in [".heic", ".heif"]:
                        content_type = "image/heic"
                    client.put_object(
                        Bucket=get_bucket_name(),
                        Key=object_key,
                        Body=file_bytes,
                        ContentType=content_type
                    )
                else:
                    # ローカルモード: ファイルに保存
                    INPUT_DIR.mkdir(parents=True, exist_ok=True)
                    save_path = INPUT_DIR / fname
                    with open(save_path, "wb") as f:
                        f.write(file_bytes)
                    # Convert HEIC
                    _convert_heic_to_jpg(save_path)
                count += 1
            st.success(f"✅ {count}枚の画像を送信しました！\nPC側で解析を行ってください。")
            
    # Inbox Status (Read-only)
    if USE_CLOUD_BACKEND:
        # クラウドモード: R2のinboxから件数取得
        from logic.storage import list_images
        inbox_files = list_images("inbox/")
    else:
        inbox_files = sorted([
            f for f in INPUT_DIR.glob("*")
            if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".heif"}
        ])
    if inbox_files:
        st.divider()
        st.caption(f"現在のInbox: {len(inbox_files)} 枚の未処理画像が待機中")


# ─────────────────────────────────────────────
# ページ設定
# ─────────────────────────────────────────────
st.set_page_config(page_title="レシートリーダー", layout="wide", page_icon="🧾")

# ─────────────────────────────────────────────
# セッション初期化
# ─────────────────────────────────────────────
if "records" not in st.session_state:
    st.session_state.records = []
if "original_data" not in st.session_state:
    st.session_state.original_data = {}
if "summary_path" not in st.session_state:
    st.session_state.summary_path = ""
if "editing_idx" not in st.session_state:
    st.session_state.editing_idx = None
if "image_file" not in st.session_state:
    st.session_state.image_file = ""
if "user_mode" not in st.session_state:
    st.session_state.user_mode = None  # None, "mobile", "pc"

# ─────────────────────────────────────────────
# ランディングページ: モード未選択時
# ─────────────────────────────────────────────
if st.session_state.user_mode is None:
    st.markdown("""
    <style>
    .landing-container {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-height: 60vh;
        text-align: center;
    }
    .landing-title {
        font-size: 2.5rem;
        margin-bottom: 1rem;
    }
    .landing-subtitle {
        font-size: 1.2rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .mode-button {
        font-size: 1.5rem !important;
        padding: 2rem 3rem !important;
        margin: 0.5rem !important;
        border-radius: 1rem !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<div class='landing-container'>", unsafe_allow_html=True)
    st.markdown("<div class='landing-title'>🧾 レシートリーダー</div>", unsafe_allow_html=True)
    st.markdown("<div class='landing-subtitle'>どのデバイスで使いますか？</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📱 スマホで撮影", use_container_width=True, type="secondary"):
            st.session_state.user_mode = "mobile"
            st.rerun()
    with col2:
        if st.button("💻 PCで確認・入力", use_container_width=True, type="primary"):
            st.session_state.user_mode = "pc"
            st.rerun()
    
    st.caption("※ スマホ: 撮影＆アップロードのみ。解析・編集はPC側で行います。")
    st.stop()

# ─────────────────────────────────────────────
# モバイルモード: 撮影専用UI
# ─────────────────────────────────────────────
if st.session_state.user_mode == "mobile":
    render_mobile_mode()
    st.divider()
    if st.button("🔄 PCモードに切り替え"):
        st.session_state.user_mode = "pc"
        st.rerun()
    st.stop()

# ─────────────────────────────────────────────
# サイドバー: 設定・履歴
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🧾 レシート管理")
    
    # Mode Switcher (compact)
    if st.button("📱 スマホモードへ切替", use_container_width=True):
        st.session_state.user_mode = "mobile"
        st.rerun()
    
    # Inbox counter (PC awareness)
    if USE_CLOUD_BACKEND:
        # クラウドモード: R2のinboxから件数取得
        from logic.storage import list_images
        inbox_count = len(list_images("inbox/"))
    else:
        # ローカルモード
        inbox_count = len([
            f for f in INPUT_DIR.glob("*")
            if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".heif"}
        ])
    if inbox_count > 0:
        st.warning(f"📥 **未処理 Inbox: {inbox_count}枚**")
        if st.button("🔄 更新"):
            st.rerun()
    else:
        st.success("✅ Inbox: 空")
        
    st.divider()

    # 🛠️ 設定・履歴 (Expanderに隠す)
    with st.expander("🛠️ 設定・履歴・バックアップ", expanded=False):
        st.caption("📂 **過去の履歴を開く**")
        sessions = _find_sessions()
        
        if not sessions:
            st.caption("履歴なし")
        else:
            options = [s["path"] for s in sessions]
            def _fmt(s):
                base = s["dir"]
                if s["file"]:
                    base += f" ({s['file']})"
                return base

            # Selectbox logic
            current_idx = 0
            if st.session_state.summary_path in options:
                current_idx = options.index(st.session_state.summary_path)
                
            selected_path_str = st.selectbox(
                "履歴を選択",
                options,
                index=current_idx,
                format_func=lambda x: _fmt(next(s for s in sessions if s["path"]==x))
            )
            
            # Auto-Load Latest if empty
            if not st.session_state.summary_path and options:
                 selected_path_str = options[0]
            
            # Load if changed
            if st.session_state.summary_path != selected_path_str:
                records, data = _load_records(selected_path_str)
                st.session_state.records = records
                st.session_state.original_data = data
                st.session_state.summary_path = selected_path_str
                st.session_state.editing_idx = None
                st.session_state.current_session_dir = str(Path(selected_path_str).parent)
                st.rerun()

        st.divider()
        
        # Backup
        if st.session_state.summary_path:
             if st.button("💾 現在のデータをバックアップ"):
                try:
                    import shutil
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"summary_backup_{ts}.json"
                    src = Path(st.session_state.summary_path)
                    dst = src.parent / backup_name
                    shutil.copy(src, dst)
                    st.success(f"バックアップ完了: {backup_name}")
                except Exception as e:
                    st.error(f"失敗: {e}")
        
        st.divider()
        
        # Manual Add
        if st.button("➕ 手動で空レコードを追加"):
            new_rec = ReceiptRecord(
                date="2026/01/01",
                vendor="手動入力",
                total_amount=0,
                tax_rate_detected=TaxRate.UNKNOWN,
                category=Category.UNKNOWN,
                payment_method=PaymentMethod.UNKNOWN,
                needs_review=True,
                missing_fields=["date", "vendor", "total_amount"]
            )
            st.session_state.records.append(new_rec)
            st.session_state.editing_idx = len(st.session_state.records) - 1
            st.success("追加しました")
            st.rerun()

    # ─────────────────────────────────────────────
    # 新規解析ロジック (サイドバーではなくメインエリアの上部に表示させたいが、
    # 構造上ここで処理してメインエリアにUIを出すか、メインエリアで処理するか。
    # ここは「PC Logic」の一部なので、if mode == "PC" の下にあるべき。
    # 既存コードはトップレベルにあるので、ここから下はメインコンテンツ)
    # ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# メインコンテンツ: PC管理モード
# ─────────────────────────────────────────────

# 1. 新規解析の提案 (Inboxにファイルがある場合のみ)
if USE_CLOUD_BACKEND:
    # クラウドモード: R2のinboxからファイル一覧取得
    from logic.storage import list_images, download_image, delete_image as r2_delete
    inbox_files = list_images("inbox/")
else:
    # ローカルモード: ローカルフォルダから取得
    inbox_files = sorted([
        f.name for f in INPUT_DIR.glob("*") 
        if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".heic"}
    ])

if inbox_files:
    # 目立つように表示
    with st.container():
        st.info(f"📥 **Inboxに {len(inbox_files)} 枚の未処理レシートがあります**")
        col_act, col_info = st.columns([1, 2])
        
        with col_act:
             if st.button("🚀 Inboxの画像をすべて解析する (Batch Run)", type="primary", use_container_width=True):
                status_container = st.status("AIがレシートを解析中...", expanded=True)
                
                try:
                    all_new_records = []
                    processed_count = 0
                    
                    # Session output setup
                    if USE_CLOUD_BACKEND:
                        # クラウドモード: DBにセッション作成
                        session_id = data_layer.create_session()
                        status_container.write(f"☁️ クラウドセッション作成: {session_id[:8]}...")
                    else:
                        # ローカルモード: フォルダ作成
                        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                        out_dir = BASE_OUTPUT_DIR / session_id
                        out_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Prepare done directory (ローカルモード用、クラウドでも使う)
                    done_dir = INPUT_DIR.parent / "done"
                    done_dir.mkdir(parents=True, exist_ok=True)

                    total_files = len(inbox_files)
                    progress_bar = status_container.progress(0)
                    
                    for idx, file_item in enumerate(inbox_files):
                        if USE_CLOUD_BACKEND:
                            # クラウドモード: R2からダウンロードして解析
                            object_key = file_item  # R2のオブジェクトキー（inbox/filename.jpg）
                            filename = object_key.split("/")[-1]  # ファイル名部分
                            status_container.write(f"Processing {idx+1}/{total_files}: {filename} ...")
                            
                            # R2から画像をダウンロード
                            img_data = download_image(object_key)
                            
                            # 一時ファイルに保存して解析
                            import tempfile
                            with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as tmp:
                                tmp.write(img_data)
                                tmp_path = tmp.name
                            
                            # Analyze
                            recs = analyze_receipt_image(tmp_path, use_split_scan=False)
                            
                            # R2のimagesフォルダに移動（inbox→images）
                            new_object_key = f"images/{filename}"
                            from logic.storage import get_r2_client, get_bucket_name
                            client = get_r2_client()
                            client.put_object(
                                Bucket=get_bucket_name(),
                                Key=new_object_key,
                                Body=img_data,
                                ContentType="image/jpeg"
                            )
                            image_url = get_presigned_url(new_object_key)
                            
                            for r in recs:
                                r.image_path = image_url
                                r._cloud_image_key = new_object_key
                            
                            # inboxから削除
                            r2_delete(object_key)
                            
                            # 一時ファイル削除
                            os.unlink(tmp_path)
                        else:
                            # ローカルモード: 従来の処理
                            filename = file_item
                            status_container.write(f"Processing {idx+1}/{total_files}: {filename} ...")
                            img_path = INPUT_DIR / filename
                            
                            # Analyze
                            recs = analyze_receipt_image(str(img_path), use_split_scan=False)
                        
                            # ローカルモード: Move to done
                            try:
                                import shutil
                                new_path = done_dir / filename
                                shutil.move(str(img_path), str(new_path))
                                
                                # CRITICAL: Update path in records to point to new location
                                for r in recs:
                                    r.image_path = str(new_path)
                                    
                            except Exception as mv_err:
                                st.warning(f"Failed to move {filename}: {mv_err}")
                                for r in recs:
                                    if not r.image_path:
                                        r.image_path = str(img_path)
                        
                        all_new_records.extend(recs)
                        
                        processed_count += 1
                        progress_bar.progress((idx + 1) / total_files)

                    status_container.write(f"✅ 解析完了: 計 {len(all_new_records)} 件のレシートを抽出しました")

                    # 保存 (Summary)
                    if USE_CLOUD_BACKEND:
                        # クラウドモード: DBに保存
                        dummy_data = {"session_id": session_id, "is_cloud": True}
                        _save_records(session_id, all_new_records, dummy_data)
                        summary_path = session_id  # クラウドではセッションIDを使用
                    else:
                        # ローカルモード: ファイル保存
                        dummy_data = {
                            "timestamp": datetime.now().isoformat(),
                            "total_receipts": len(all_new_records),
                            "valid_count": 0,
                            "invalid_count": 0,
                            "records": [],
                        }
                        summary_path = out_dir / "summary.json"
                        _save_records(str(summary_path), all_new_records, dummy_data)
                        summary_path = str(summary_path)
                    
                    status_container.update(label="完了! 編集画面へ移動します", state="complete", expanded=False)
                    
                    # 状態更新
                    st.session_state.records = all_new_records
                    st.session_state.original_data = dummy_data
                    st.session_state.summary_path = summary_path
                    
                    if all_new_records:
                        st.session_state.editing_idx = 0
                    else:
                        st.warning("レシートが見つかりませんでした。")
                    
                    st.rerun()
                    
                except Exception as e:
                    import traceback
                    status_container.update(label="エラー発生", state="error")
                    st.error(f"解析エラー: {e}")
                    st.code(traceback.format_exc(), language="text")

        with col_info:
             st.caption(f"対象ファイル: {inbox_files[0]} 他")
             
    st.divider()

# ─────────────────────────────────────────────
# メイン: 一覧画面 or 編集画面
# ─────────────────────────────────────────────
records = st.session_state.records

# Header: 現在開いているファイルパスを表示
unreviewed_idx = -1
if st.session_state.summary_path:
    st.caption(f"📂 Open: `{st.session_state.summary_path}`")
    
    # Escape Hatch
    # Escape Hatch
    if st.button("📂 レシート一覧を表示 (Escape Hatch)", key="escape_hatch"):
        st.session_state.editing_idx = None
        st.session_state.manual_list_view = True # Prevent auto-redirect
        st.rerun()

    # ─────────────────────────────────────────────
    # Auto-Reference Logic (Inbox-First)
    # ─────────────────────────────────────────────
    # 編集モードでなく、かつ未処理がある場合、強制的に編集モードへ遷移
    # ただし、意図的に一覧に戻った場合などをどう扱うか？
    # -> Session Stateに "show_list" フラグを持たせるのが良さそうだが、
    # シンプルに「editing_idx is None」なら自動遷移させる (上記ボタンで解除中は除く...は難しいので)
    # ここでは「未処理がある限り、一覧画面を開くと自動的にその編集画面に飛ばす」挙動にする
    # Escape Hatch を機能させるには、rerunせずに一覧を描画する必要があるが、
    # 構造上 editing_idx で分岐している。
    
    # 修正案: Escape Hatchが押された直後のリランではスキップするフラグが必要。
    # しかし複雑になるので、まずは「一覧画面の先頭」に「未処理の編集に戻る」ボタンを置く形にし、
    # 自動遷移は「初回ロード時」や「解析直後」に限定するか？
    
    # 今回の要件: "起動時に見えるのは「未処理Inbox」だけ"
    # -> recordsロード直後や、解析完了直後に editing_idx をセットする。
    # ここでは、「editing_idx is None」かつ「未処理あり」なら、強制遷移させる。
    # Escape Hatchを押した場合 -> editing_idx = None になる -> ここに来る -> また飛ばされる... 無限ループ
    
    # 解決策: session_state['manual_list_view'] = True を Escape Hatch でセット。
    
    if "manual_list_view" not in st.session_state:
        st.session_state.manual_list_view = False

    # 未処理(未確認かつゴミ箱でない)を探す
    unreviewed_idx = -1
    for i, r in enumerate(records):
        if not r.is_confirmed and not r.is_discarded:
            unreviewed_idx = i
            break
            
    if st.session_state.editing_idx is None:
        if unreviewed_idx != -1 and not st.session_state.manual_list_view:
            st.session_state.editing_idx = unreviewed_idx
            st.rerun()
else:
    st.info("👈 サイドバーからデータを選択、または新規解析を実行してください")

if st.session_state.editing_idx is None:
    # ═══════════════════════════════════════════
    #  一覧画面
    # ═══════════════════════════════════════════
    st.subheader("📋 レシート一覧")

    # マージ統計 (少し控えめに)
    with st.expander("📊 統計情報・マージ状況", expanded=False):
        _render_merge_stats(records)

    # Escape Hatch Logic (Reset manual view when picking a row)
    # ...

    if unreviewed_idx != -1:
         st.info(f"💡 残り {sum(1 for r in records if not r.is_confirmed and not r.is_discarded)} 件の未処理レシートがあります。")
         if st.button(f"⚡️ 次の未処理レシート ({unreviewed_idx + 1}番目) を開く", type="primary"):
             st.session_state.manual_list_view = False
             st.session_state.editing_idx = unreviewed_idx
             st.rerun()

    # フィルタ
    filter_mode = st.radio(
        "表示フィルタ",
        ["すべて", "要対応 (未確認)", "確認済み (完了)", "ゴミ箱"],
        horizontal=True,
        index=1 # Default to Needs Review
    )

    # テーブル表示
    if not records:
         st.caption("レコードがありません。新規解析を行ってください。")
    
    # Header Row
    with st.container():
        cols = st.columns([0.5, 2, 2, 1.5, 1.5, 2, 1])
        cols[0].caption("状態")
        cols[1].caption("支払先")
        cols[2].caption("件名")
        cols[3].caption("日付")
        cols[4].caption("金額")
        cols[5].caption("インボイス(T番号)")
        cols[6].caption("編集")
        st.divider()

    for i, rec in enumerate(records):
        status = _get_status(rec)
        emoji = _status_emoji(status)

        # フィルタ適用
        if filter_mode == "ゴミ箱":
             if not rec.is_discarded: continue
        else:
             # 通常表示: ゴミ箱に入っているものは隠す
             if rec.is_discarded: continue
             
             if filter_mode == "要対応 (未確認)":
                  if rec.is_confirmed: continue
             elif filter_mode == "確認済み (完了)":
                  if not rec.is_confirmed: continue

        # T番号表示
        if rec.invoice_no_norm:
            t_display = f"✅ {rec.invoice_no_norm}"
        elif rec.invoice_candidate:
            t_display = f"🔶 {rec.invoice_candidate} (候補)"
        else:
            t_display = "—"

        # バッジ的なスタイル
        # statusに応じた背景色は Streamlit ネイティブでは難しいので emoji で対応
        
        # カード風レイアウト
        with st.container():
            cols = st.columns([0.5, 2, 2, 1.5, 1.5, 2, 1])
            cols[0].write(f"### {emoji}")
            cols[1].write(f"**{rec.vendor}**")
            cols[2].write(rec.subject or "—")
            cols[3].write(rec.date or "日付なし")
            cols[4].write(f"¥{rec.total_amount:,}")
            cols[5].write(t_display)
            
            # マージバッジ & Backend
            if rec.merge_candidates:
                cols[1].caption(f"📚 {len(rec.merge_candidates)}枚マージ済")
            
            if cols[6].button("✏️", key=f"edit_{i}"):
                st.session_state.editing_idx = i
                st.rerun()

             # マージ詳細 (必要な時だけ)
            if rec.merge_candidates or rec.backend_used:
                # Expanderは煩わしいので、詳細を知りたい時だけ見れるようにしたいが、
                # ここではシンプルに caption で済ませる
                pass

            st.divider()

    # サマリー
    total = len(records)
    # Filter by confirmed if needed, or valid
    # User Request: "review_done" only
    valid_records = [r for r in records if r.is_confirmed and not r.is_discarded]
    discarded_cnt = sum(1 for r in records if r.is_discarded)
    
    confirmed_cnt = len(valid_records)
    
    # -------------------------------------------------------------
    # CSV Download Section
    # -------------------------------------------------------------
    
    st.markdown("### 📥 データ出力")
    
    col_csv, col_extra = st.columns([2, 1])
    
    with col_csv:
        # Valid records for CSV
        # 必須: is_confirmed (確認済) かつ valid (必須項目OK) かつ ゴミ箱でない
        valid_csv_records = [r for r in records if r.is_confirmed and not r.is_discarded and _get_status(r) == "valid"]
        
        if valid_csv_records:
            # Generate CSV
            csv_data = generate_csv_data(valid_csv_records)
            df = pd.DataFrame(csv_data["valid"])
            
            # Timestamp for filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_filename = f"receipt_export_{timestamp}.csv"
            
            # Auto-Backup (Server-side) e.g. output/2026.../latest_export_backup.csv
            if st.session_state.summary_path:
                try:
                    out_dir = Path(st.session_state.summary_path).parent
                    backup_path = out_dir / "latest_export_backup.csv"
                    df.to_csv(backup_path, index=False, encoding="utf-8-sig")
                except:
                    pass
            
            # Use HTML link fallback for stability
            csv_str_encoded = df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
            b64 = base64.b64encode(csv_str_encoded).decode()
            
            # Change style to Blue and add distinct label to verify update
            href = f'''
            <a href="data:text/csv;base64,{b64}" download="{export_filename}" target="_blank" 
               style="text-decoration:none; display:inline-block; padding:0.6em 1.2em; background-color:#007BFF; color:white; border-radius:4px; font-weight:bold;">
               💾 CSV出力 ({len(valid_csv_records)}件)
            </a>
            '''
            
            st.markdown(href, unsafe_allow_html=True)
            st.caption(f"全 {len(valid_csv_records)} 件のデータをダウンロードします。")
            
            # Panic Button (Expander)
            with st.expander("⚠️ ダウンロードできない場合"):
                 st.write("ブラウザの制限でダウンロードできない場合は、以下のボタンを押すとMacの「ダウンロード」フォルダに直接保存します。")
                 if st.button("⚡️ Macのダウンロードフォルダに直接保存", key="panic_save_btn"):
                    try:
                        home = Path.home()
                        downloads_dir = home / "Downloads"
                        save_path = downloads_dir / export_filename
                        df.to_csv(save_path, index=False, encoding="utf-8-sig")
                        st.success(f"保存しました: {save_path}")
                        subprocess.run(["open", "-R", str(save_path)])
                    except Exception as e:
                        st.error(f"保存失敗: {e}")

        else:
            if confirmed_cnt > 0:
                st.warning("⚠️ 確認済み(完了)のレシートはありますが、必須項目が不足しているためCSV出力できません。「編集」ボタンから修正してください。")
            else:
                st.info("ℹ️ CSV出力するには、レシートの内容を確認し「確認完了」チェックを入れてください。")

else:
    # ═══════════════════════════════════════════
    #  編集画面
    # ═══════════════════════════════════════════
    idx = st.session_state.editing_idx
    rec = records[idx]
    status = _get_status(rec)
    emoji = _status_emoji(status)

    st.title(f"{emoji} レシート編集 [{idx + 1}/{len(records)}]")

    # ナビゲーション: 前/次
    # ナビゲーション: 前/次
    nav_cols = st.columns([1, 1, 4, 2])
    with nav_cols[0]:
        if idx > 0 and st.button("◀ 前"):
            st.session_state.editing_idx = idx - 1
            st.rerun()
    with nav_cols[1]:
        if idx < len(records) - 1 and st.button("次 ▶"):
            st.session_state.editing_idx = idx + 1
            st.rerun()
            
    with nav_cols[3]:
        # Trash Button
        if rec.is_discarded:
             if st.button("♻️ 復元する"):
                 rec.is_discarded = False
                 st.session_state.records = records
                 _save_records(st.session_state.summary_path, records, st.session_state.original_data)
                 st.success("復元しました")
                 st.rerun()
        else:
             if st.button("🗑 削除 (ゴミ箱へ)", type="secondary"):
                 rec.is_discarded = True
                 # 確認済フラグも外す？ 要件次第だが、ゴミ箱行きなら確認も何もないので外しておくのが無難
                 rec.is_confirmed = False 
                 
                 st.session_state.records = records
                 _save_records(st.session_state.summary_path, records, st.session_state.original_data)
                 
                 # Auto Next
                 # 次の未処理へ
                 next_idx = None
                 for i in range(len(records)):
                    if not records[i].is_confirmed and not records[i].is_discarded and i != idx:
                         next_idx = i
                         break
                 
                 if next_idx is not None:
                     st.session_state.editing_idx = next_idx
                     st.toast("ゴミ箱に移動しました。次のレシートを表示します。")
                     st.rerun()
                 else:
                     st.success("全てのレシートを処理しました！一覧に戻ります。")
                     st.session_state.editing_idx = None
                     st.rerun()

    # 2カラム: 左=画像, 右=フォーム
    col_img, col_form = st.columns([1, 1])
    with col_img:
        target_img_path = None
        
        # Candidates (Sort by Name)
        candidates = sorted(list(DONE_DIR.glob("*")) + list(INPUT_DIR.glob("*")), key=lambda p: p.name)
        candidate_names = [p.name for p in candidates if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic"}]
        
        # Determine initial index
        # 1. Try existing record path (Extract filename from full path)
        current_selection = rec.image_path
        current_filename = Path(current_selection).name if current_selection else ""
        
        default_index = 0
        
        if current_filename and current_filename in candidate_names:
            default_index = candidate_names.index(current_filename) + 1 # +1 for (Unselected)
        else:
            # 2. Heuristic: Match index (Only if no path match)
            # CAUTION: This causes mismatch if records != files. 
            # Better to default to 0 (Unselected) or try harder.
            # But kept as fallback for raw uploads.
            # However, with the batch fix, paths should be correct.
            # If path is wrong, showing ANY image is dangerous. 
            # Let's disable heuristic index matching to prevent "Wrong Image" confusion.
            default_index = 0
        
        # Selectbox (Always visible)
        selected_img = st.selectbox(
            "画像ファイル (変更で即保存)", 
            ["(未選択)"] + candidate_names, 
            index=default_index, 
            key=f"img_sel_{idx}"
        )
        
        # Resolve Path
        if selected_img != "(未選択)":
            for p in candidates:
                if p.name == selected_img:
                    target_img_path = str(p)
                    break
        
        # Auto-Save if changed (or if heuristic filled it in first time)
        if selected_img != "(未選択)" and selected_img != rec.image_path:
            rec.image_path = selected_img
            st.session_state.records = records
            _save_records(st.session_state.summary_path, records, st.session_state.original_data)
            st.toast(f"画像を紐付けました: {selected_img}")
            # rerunning might be annoying if it happens automatically, but necessary to sync state
            st.rerun()

        # Render Image
        if target_img_path and Path(target_img_path).exists():
             _render_zoomable_image(target_img_path)
        else:
             st.info("画像が選択されていません")

    with col_form:
        st.subheader("📝 編集フォーム")
        
        # ユーザー要望: ファイル名を表示して確認できるようにする
        current_linked_file = Path(rec.image_path).name if rec.image_path else "（未設定）"
        st.info(f"📄 対象ファイル: **{current_linked_file}**")

        # 不足項目の表示
        if rec.missing_fields:
            st.warning(f"不足項目: {', '.join(rec.missing_fields)}")

        with st.form(key=f"edit_form"):
            # 日付
            new_date = st.text_input("日付 (YYYY/MM/DD)", value=rec.date)
            
            # 支払先
            new_vendor = st.text_input("支払先", value=rec.vendor)

            # 件名
            new_subject = st.text_input("件名", value=rec.subject)

            # 金額
            new_amount = st.number_input(
                "税込総額",
                value=rec.total_amount,
                min_value=0,
                step=100,
            )

            # 税率
            tax_options = [TaxRate.RATE_10, TaxRate.RATE_8, TaxRate.RATE_8_REDUCED, TaxRate.EXEMPT, TaxRate.UNKNOWN]
            tax_labels = ["10%", "8%", "8% 軽減", "非課税 (Exempt)", "不明"]
            current_tax_idx = tax_options.index(rec.tax_rate_detected) if rec.tax_rate_detected in tax_options else 4
            new_tax_idx = st.selectbox(
                "税率",
                range(len(tax_options)),
                index=current_tax_idx,
                format_func=lambda i: tax_labels[i],
            )

            # 支払方法
            pay_options = [PaymentMethod.CASH, PaymentMethod.PAYPAY, PaymentMethod.CREDIT, PaymentMethod.UNKNOWN]
            pay_labels = ["現金 (cash)", "PayPay", "クレジット (credit)", "不明"]
            current_pay_idx = pay_options.index(rec.payment_method) if rec.payment_method in pay_options else 3
            new_pay_idx = st.selectbox(
                "支払方法",
                range(len(pay_options)),
                index=current_pay_idx,
                format_func=lambda i: pay_labels[i],
            )

            # カテゴリ
            cat_options = list(Category)
            cat_labels = {
                Category.TRAVEL: "旅費交通費",
                Category.PARKING: "駐車場",
                Category.TOLL: "高速・通行料",
                Category.MEETING: "会議費",
                Category.ENTERTAINMENT: "交際費",
                Category.SUPPLIES: "消耗品費",
                Category.DUES: "諸会費",
                Category.OTHER: "雑費",
                Category.UNKNOWN: "未設定",
            }
            current_cat_idx = cat_options.index(rec.category) if rec.category in cat_options else len(cat_options) - 1
            new_cat_idx = st.selectbox(
                "カテゴリ",
                range(len(cat_options)),
                index=current_cat_idx,
                format_func=lambda i: f"{cat_labels.get(cat_options[i], cat_options[i].value)}",
            )

            # T番号セクション
            st.markdown("---")
            st.caption("適格請求書発行事業者登録番号 (T番号)")
            
            # 手動入力欄 (候補反映用)
            current_invoice = rec.invoice_no_norm or ""
            
            # 候補がある場合のUI
            confirm_candidate = False
            if rec.invoice_candidate and not rec.invoice_no_norm:
                st.info(f"💡 AI提案: `{rec.invoice_candidate}`")
                confirm_candidate = st.checkbox("この候補を採用する", value=False)

            new_invoice = st.text_input(
                "T番号 (T+13桁)",
                value=current_invoice,
                placeholder="例: T1234567890123",
                help="手入力する場合はこちら。候補を採用する場合は上のチェックを入れてください。"
            )

            # ---------------------------------------------------------
            # Validation Status Display
            # ---------------------------------------------------------
            st.markdown("---")
            is_valid = not rec.missing_fields
            
            if is_valid:
                st.success("✅ 必須項目OK")
                # Confirmed checkbox
                new_is_confirmed = st.checkbox("確認完了 (これを含めてCSV出力)", value=rec.is_confirmed)
            else:
                st.error(f"❌ 未入力: {', '.join(rec.missing_fields)}")
                st.caption("※全ての必須項目を埋めると「確認完了」チェックが可能になります。")
                new_is_confirmed = False

            st.divider()

            # ---------------------------------------------------------
            # Action Buttons
            # ---------------------------------------------------------
            col_save, col_next = st.columns([1, 1])
            
            # Note: Form submit buttons return True if clicked.
            # We must handle logic for each.
            
            with col_save:
                btn_save = st.form_submit_button("保存して一覧に戻る", use_container_width=True)
            
            with col_next:
                btn_next = st.form_submit_button("保存して次へ (Save & Next) ➡", type="primary", use_container_width=True)

            # Logic Handlers
            if btn_save or btn_next:
                # 1. Update Record Object from Form Data
                rec.date = new_date.strip()
                rec.vendor = new_vendor.strip()
                rec.subject = new_subject.strip()
                rec.total_amount = int(new_amount)
                rec.tax_rate_detected = tax_options[new_tax_idx]
                rec.payment_method = pay_options[new_pay_idx]
                rec.category = cat_options[new_cat_idx]
                rec.is_confirmed = new_is_confirmed

                # T-Number Logic
                if confirm_candidate and rec.invoice_candidate:
                    rec.invoice_no_norm = rec.invoice_candidate
                    rec.qualified_flag = "○"
                elif new_invoice.strip():
                    rec.invoice_no_norm = new_invoice.strip()
                    rec.qualified_flag = "○"
                else:
                    # Clear if input is empty and not confirming candidate
                    # (Only if user explicitly cleared it)
                    if not current_invoice and not new_invoice:
                         # Was empty, stays empty
                         pass
                    elif current_invoice and not new_invoice:
                         # User cleared it
                         rec.invoice_no_norm = ""
                         rec.qualified_flag = ""

                # Re-validate
                revalidate_record(rec)
                
                # Update Session State
                st.session_state.records[idx] = rec
                _save_records(st.session_state.summary_path, st.session_state.records, st.session_state.original_data)
                
                if btn_save:
                    st.success("保存しました")
                    st.session_state.editing_idx = None
                    st.rerun()
                    
                elif btn_next:
                    # Find next unconfirmed record
                    next_idx = None
                    # Search after current index
                    for i in range(idx + 1, len(records)):
                        if not records[i].is_confirmed and not records[i].is_discarded:
                            next_idx = i
                            break
                    # If not found, wrap around from 0
                    if next_idx is None:
                        for i in range(0, idx):
                            if not records[i].is_confirmed and not records[i].is_discarded:
                                next_idx = i
                                break
                    
                    if next_idx is not None:
                        st.session_state.editing_idx = next_idx
                        st.toast(f"Saved! Moving to {next_idx + 1}/{len(records)}")
                        st.rerun()
                    else:
                        st.balloons()
                        st.success("🎉 全てのレシートの確認が完了しました！")
                        st.info("一覧画面に戻ります...")
                        st.session_state.editing_idx = None
                        st.rerun()

