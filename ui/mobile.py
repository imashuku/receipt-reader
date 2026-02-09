import streamlit as st
import uuid
import socket
from pathlib import Path
from datetime import datetime
from logic.session_manager import find_sessions, load_records, save_records, INPUT_DIR
from logic.gemini_client import analyze_receipt_image
from ui.shared import get_local_ip, convert_heic_to_jpg, render_zoomable_image, status_emoji, get_status
from ui.styles import (
    MODERN_CSS, render_step_indicator, render_stats_bar,
    render_receipt_card, render_empty_state
)


def render_mobile(use_cloud: bool):
    st.markdown(MODERN_CSS, unsafe_allow_html=True)

    # ── Header ──
    st.markdown("""
    <div style="text-align:center; padding: 0.5rem 0 0;">
        <span style="font-size:28px">🧾</span>
        <h1 style="font-size:20px; font-weight:600; color:#202124; margin:4px 0 0;">
            レシートリーダー
        </h1>
        <p style="font-size:12px; color:#80868b; margin:0;">AT Cars 経費処理</p>
    </div>
    """, unsafe_allow_html=True)

    # ── Determine current step ──
    sessions = find_sessions(use_cloud)
    has_inbox = False
    if use_cloud:
        from logic.storage import list_images
        has_inbox = len(list_images("inbox/")) > 0
    else:
        inbox_dir = INPUT_DIR
        has_inbox = inbox_dir.exists() and len(list(inbox_dir.glob("*"))) > 0

    # Determine step: 1=upload, 2=confirm, 3=export
    current_step = 1
    if sessions:
        records, _ = load_records(sessions[0]["path"], use_cloud)
        review_count = sum(1 for r in records if r.needs_review or r.missing_fields)
        confirmed_count = sum(1 for r in records if r.is_confirmed)
        if records:
            current_step = 2
        if records and review_count == 0 and confirmed_count > 0:
            current_step = 3
    else:
        records = []
        review_count = 0
        confirmed_count = 0

    st.markdown(render_step_indicator(current_step), unsafe_allow_html=True)

    # ── Tab Layout ──
    tab_upload, tab_confirm, tab_export = st.tabs(["📷 アップロード", "✏️ 確認", "📥 出力"])

    # ━━━━━━━━━━ Tab 1: Upload ━━━━━━━━━━
    with tab_upload:
        st.markdown('<div class="section-title">レシート画像を追加</div>', unsafe_allow_html=True)

        with st.form("mobile_upload_form", clear_on_submit=True):
            uploaded_files = st.file_uploader(
                "撮影 または ライブラリから選択",
                type=["png", "jpg", "jpeg", "heic", "heif"],
                accept_multiple_files=True,
                key="mobile_uploader",
                label_visibility="collapsed"
            )
            submitted = st.form_submit_button(
                "📤 送信", type="primary", use_container_width=True
            )

            if submitted and uploaded_files:
                count = 0
                for vid in uploaded_files:
                    file_bytes = vid.read()
                    ext = Path(vid.name).suffix.lower()
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    uid = str(uuid.uuid4())[:8]
                    fname = f"{ts}_{uid}{ext}"

                    if use_cloud:
                        from logic.storage import get_r2_client, get_bucket_name
                        client = get_r2_client()
                        object_key = f"inbox/{fname}"
                        content_type = "image/jpeg"
                        if ext == ".png": content_type = "image/png"
                        elif ext in [".heic", ".heif"]: content_type = "image/heic"
                        client.put_object(
                            Bucket=get_bucket_name(), Key=object_key,
                            Body=file_bytes, ContentType=content_type
                        )
                    else:
                        INPUT_DIR.mkdir(parents=True, exist_ok=True)
                        save_path = INPUT_DIR / fname
                        with open(save_path, "wb") as f:
                            f.write(file_bytes)
                        convert_heic_to_jpg(save_path)
                    count += 1
                st.success(f"✅ {count}枚を送信しました")

        # Connection info (collapsed)
        with st.expander("📡 PCからアクセスする場合"):
            ips = []
            try:
                for info in socket.getaddrinfo(socket.gethostname(), None):
                    ip = info[4][0]
                    if "." in ip and not ip.startswith("127."):
                        ips.append(ip)
                ips = sorted(list(set(ips)))
            except:
                ips = [get_local_ip()]
            for ip in ips:
                st.code(f"http://{ip}:8501", language="text")

    # ━━━━━━━━━━ Tab 2: Confirm ━━━━━━━━━━
    with tab_confirm:
        if not records:
            st.markdown(
                render_empty_state("📋", "レシートがありません", "まずアップロードして解析してください"),
                unsafe_allow_html=True
            )
        else:
            # Stats
            st.markdown(
                render_stats_bar(len(records), confirmed_count, review_count),
                unsafe_allow_html=True
            )

            st.markdown('<div class="section-title">レシート一覧</div>', unsafe_allow_html=True)

            for i, rec in enumerate(records):
                if rec.is_discarded:
                    continue
                status = get_status(rec)
                cat_map = {
                    "travel": "旅費交通費", "parking": "駐車場", "toll": "通行料",
                    "meeting": "会議費", "entertainment": "交際費", "supplies": "消耗品",
                    "dues": "諸会費", "other": "その他", "unknown": "未設定"
                }
                cat_label = cat_map.get(rec.category.value, rec.category.value)

                st.markdown(
                    render_receipt_card(rec.vendor, rec.date, rec.total_amount, status, cat_label),
                    unsafe_allow_html=True
                )

                with st.expander(f"詳細・編集 #{i+1}", expanded=False):
                    if rec.image_path:
                        st.image(rec.image_path, use_container_width=True)
                    new_vendor = st.text_input("店名", value=rec.vendor, key=f"m_v_{i}")
                    new_amount = st.number_input("金額", value=rec.total_amount, key=f"m_a_{i}")
                    st.caption("💡 詳細な編集はPCモードで行えます")

    # ━━━━━━━━━━ Tab 3: Export ━━━━━━━━━━
    with tab_export:
        if not records:
            st.markdown(
                render_empty_state("📄", "出力データなし", "レシートを解析・確認してください"),
                unsafe_allow_html=True
            )
        else:
            from logic.exporter import generate_csv_data
            import pandas as pd

            csv_result = generate_csv_data(records)
            valid_rows = csv_result.get("valid", [])
            invalid_rows = csv_result.get("invalid", [])

            st.markdown(f"""
            <div class="modern-card">
                <div style="text-align:center">
                    <div style="font-size:36px; margin-bottom:8px">📄</div>
                    <div style="font-size:20px; font-weight:700; color:#202124">{len(valid_rows)}件</div>
                    <div style="font-size:12px; color:#80868b">出力可能なレコード</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            if valid_rows:
                import io
                df_csv = pd.DataFrame(valid_rows)
                csv_bytes = df_csv.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "📥 CSVダウンロード",
                    data=csv_bytes,
                    file_name="receipts.csv",
                    mime="text/csv",
                    use_container_width=True,
                    type="primary"
                )
            else:
                st.info("確認済みのレシートがまだありません。")

            if invalid_rows:
                with st.expander(f"⚠️ 不備あり: {len(invalid_rows)}件"):
                    for row in invalid_rows:
                        st.caption(f"• {row.get('摘要', '不明')} — 不足: {', '.join(row.get('_error_reasons', []))}")

    # ── Footer: Mode Switch ──
    st.markdown("---")
    if st.button("💻 PCモードへ切り替え", use_container_width=True):
        st.session_state.user_mode = "desktop"
        st.rerun()
