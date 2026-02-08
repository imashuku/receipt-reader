import streamlit as st
import uuid
import socket
from pathlib import Path
from datetime import datetime
from logic.session_manager import find_sessions, load_records, save_records, INPUT_DIR
from ui.shared import get_local_ip, convert_heic_to_jpg, render_zoomable_image, status_emoji

def render_mobile(use_cloud: bool):
    st.markdown("""
    <style>
    .mobile-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 0.5rem;
        border-left: 5px solid #ccc;
    }
    .mobile-card.valid { border-left-color: #0c0; }
    .mobile-card.review { border-left-color: #fc0; }
    .mobile-card.invalid { border-left-color: #f00; }
    
    .mobile-upload-area {
        border: 2px dashed #4CAF50;
        background-color: #e8f5e9;
        padding: 20px;
        text-align: center;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("📱 レシートリーダー (Mobile)")
    
    # ── Connection Info ──
    with st.expander("📡 接続情報 (PCで開く場合)", expanded=False):
        ips = []
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                 ip = info[4][0]
                 if "." in ip and not ip.startswith("127."):
                     ips.append(ip)
            ips = sorted(list(set(ips)))
        except:
            ips = [get_local_ip()]
        
        st.caption("同一Wi-Fi内のPCからアクセス可能:")
        for ip in ips:
            st.code(f"http://{ip}:8501", language="text")

    # ── Upload Section ──
    st.markdown("### 📷 レシート追加")
    st.info("iPhoneで撮影・アップロードしてください。解析はPC/クラウドで行われます。")
    
    with st.form("mobile_upload_form", clear_on_submit=True):
        uploaded_files = st.file_uploader(
            "カメラで撮影 または ライブラリから選択",
            type=["png", "jpg", "jpeg", "heic", "heif"],
            accept_multiple_files=True,
            key="mobile_uploader"
        )
        submitted = st.form_submit_button("📤 送信 (Inboxへ)", type="primary", use_container_width=True)
        
        if submitted and uploaded_files:
            count = 0
            for vid in uploaded_files:
                file_bytes = vid.read()
                ext = Path(vid.name).suffix.lower()
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                uid = str(uuid.uuid4())[:8]
                fname = f"{ts}_{uid}{ext}"
                
                if use_cloud:
                    # クラウドモード: R2へアップロード
                    # 遅延インポートで循環回避
                    from logic.storage import get_r2_client, get_bucket_name
                    client = get_r2_client()
                    object_key = f"inbox/{fname}"
                    content_type = "image/jpeg"
                    if ext == ".png": content_type = "image/png"
                    elif ext in [".heic", ".heif"]: content_type = "image/heic"
                    
                    client.put_object(
                        Bucket=get_bucket_name(),
                        Key=object_key,
                        Body=file_bytes,
                        ContentType=content_type
                    )
                else:
                    # ローカルモード
                    INPUT_DIR.mkdir(parents=True, exist_ok=True)
                    save_path = INPUT_DIR / fname
                    with open(save_path, "wb") as f:
                        f.write(file_bytes)
                    convert_heic_to_jpg(save_path)
                count += 1
            st.success(f"✅ {count}枚を送信しました")

    # ── Recent Session Viewer ──
    st.divider()
    st.markdown("### 📋 最新のレシート (確認待ち)")
    
    sessions = find_sessions(use_cloud)
    if not sessions:
        st.write("まだ履歴がありません。")
        return

    # 最新セッションを自動選択
    latest_session = sessions[0]
    st.caption(f"Session: {latest_session['timestamp']} ({latest_session['dir']})")
    
    # データのロード
    records, original_data = load_records(latest_session["path"], use_cloud)
    
    # 確認待ちフィルター
    review_records = [r for r in records if r.needs_review or r.missing_fields]
    valid_records = [r for r in records if not r.needs_review and not r.missing_fields]
    
    # 表示（カード形式）
    if not records:
        st.info("レシートデータはありません。")
    elif not review_records:
        st.success("🎉 全て確認済みです！")
    
    target_records = review_records if review_records else valid_records[:5] # 確認待ちがなければ最新5件を表示
    
    for i, rec in enumerate(target_records):
        status_class = "review" if rec.needs_review else ("valid" if not rec.missing_fields else "invalid")
        
        with st.container():
            col1, col2 = st.columns([1, 3])
            with col1:
                # サムネイル（簡易）: 本来は縮小版が欲しいが、そのまま表示
                 if rec.image_path:
                    st.image(rec.image_path, width=80)
                 else:
                    st.write("No IMG")
            with col2:
                st.markdown(f"""
                **{rec.vendor or '不明な店舗'}**  
                📅 {rec.date} / ¥{rec.total_amount:,}  
                {status_emoji(status_class)} {rec.category.value}
                """, unsafe_allow_html=True)
                
                # 詳細編集ボタン（Expanderで簡易実装）
                with st.expander("詳細・編集"):
                     st.image(rec.image_path) # Full view
                     new_vendor = st.text_input("店名", value=rec.vendor, key=f"m_vendor_{i}")
                     new_amount = st.number_input("金額", value=rec.total_amount, key=f"m_amount_{i}")
                     # 保存ボタンは未実装（必要なら session_manager.save_records を呼ぶ）
                     st.info("モバイルでの編集機能は簡易版です。詳細はPCで確認してください。")

    if st.button("🔄 PCモードへ切り替え"):
        st.session_state.user_mode = "pc"
        st.rerun()
