import streamlit as st
import pandas as pd
import json
from pathlib import Path
from datetime import datetime
import uuid
import sys

# Logic Imports
try:
    from logic.models import ReceiptRecord, TaxRate, PaymentMethod, Category
    from logic import session_manager
    from logic.exporter import generate_csv_data
    from logic.gemini_client import analyze_receipt_image, rescan_specific_area
except ImportError:
    if "logic.models" in sys.modules:
        ReceiptRecord = sys.modules["logic.models"].ReceiptRecord
        TaxRate = sys.modules["logic.models"].TaxRate
        PaymentMethod = sys.modules["logic.models"].PaymentMethod
        Category = sys.modules["logic.models"].Category
    if "logic.session_manager" in sys.modules:
        session_manager = sys.modules["logic.session_manager"]
    if "logic.exporter" in sys.modules:
        generate_csv_data = sys.modules["logic.exporter"].generate_csv_data
    if "logic.gemini_client" in sys.modules:
        analyze_receipt_image = sys.modules["logic.gemini_client"].analyze_receipt_image
        rescan_specific_area = sys.modules["logic.gemini_client"].rescan_specific_area

# UI Imports
from ui.shared import render_zoomable_image, status_emoji, convert_heic_to_jpg, get_status
from ui.styles import (
    MODERN_CSS, render_step_indicator, render_stats_bar,
    render_receipt_card, render_empty_state
)

# ── Maps ──
CAT_MAP = {
    Category.TRAVEL: "旅費交通費", Category.PARKING: "駐車場",
    Category.TOLL: "高速・通行料", Category.MEETING: "会議費",
    Category.ENTERTAINMENT: "交際費", Category.SUPPLIES: "消耗品費",
    Category.DUES: "諸会費", Category.OTHER: "その他", Category.UNKNOWN: "未設定"
}
PAY_MAP = {
    PaymentMethod.CASH: "現金", PaymentMethod.PAYPAY: "PayPay",
    PaymentMethod.CREDIT: "クレジットカード", PaymentMethod.UNKNOWN: "不明"
}
TAX_MAP = {
    TaxRate.RATE_10: "10%", TaxRate.RATE_8: "8%",
    TaxRate.RATE_8_REDUCED: "8% (軽減)", TaxRate.EXEMPT: "免税", TaxRate.UNKNOWN: "不明"
}


def render_desktop(use_cloud: bool):
    st.markdown(MODERN_CSS, unsafe_allow_html=True)

    # ── Sidebar ──
    with st.sidebar:
        st.markdown("""
        <div style="padding:0.5rem 0 1rem; text-align:center;">
            <span style="font-size:32px">🧾</span>
            <h2 style="font-size:16px; font-weight:600; margin:4px 0 0; color:#202124;">
                レシートリーダー
            </h2>
            <p style="font-size:11px; color:#80868b; margin:0;">AT Cars 経費処理</p>
        </div>
        """, unsafe_allow_html=True)

        if st.button("📱 モバイルモードへ", use_container_width=True):
            st.session_state.user_mode = "mobile"
            st.rerun()

        st.markdown("---")

        # Inbox count
        inbox_count = 0
        if use_cloud:
            from logic.storage import list_images
            inbox_count = len(list_images("inbox/"))
        else:
            inbox_dir = session_manager.INPUT_DIR
            if inbox_dir.exists():
                inbox_count = len(list(inbox_dir.glob("*")))

        if inbox_count > 0:
            st.markdown(f"""
            <div class="modern-card" style="background:#fef7e0; border:1px solid #fbbc04;">
                <div style="text-align:center;">
                    <div style="font-size:20px; font-weight:700; color:#b06000;">{inbox_count}</div>
                    <div style="font-size:11px; color:#b06000;">未処理の画像</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        # Session Selector
        st.markdown('<div class="section-title">セッション</div>', unsafe_allow_html=True)
        sessions = session_manager.find_sessions(use_cloud)
        session_options = {
            f"{s['timestamp']}  ({s['total']}枚)": s
            for s in sessions
        }

        selected_key = st.selectbox(
            "セッション選択",
            options=list(session_options.keys()) if sessions else [],
            index=0 if sessions else None,
            label_visibility="collapsed"
        )

        if st.button("＋ 新規セッション", use_container_width=True, type="primary"):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if use_cloud:
                from logic import data_layer
                sid = f"sess_{ts}_{str(uuid.uuid4())[:8]}"
                data_layer.create_session(sid)
                st.session_state.current_session_dir = sid
            else:
                new_dir = session_manager.BASE_OUTPUT_DIR / ts
                new_dir.mkdir(parents=True, exist_ok=True)
                st.session_state.current_session_dir = str(new_dir)
            st.rerun()

    # ── Load Session Data ──
    current_session = None
    if selected_key:
        current_session = session_options[selected_key]
        st.session_state.summary_path = current_session["path"]

        if "records" not in st.session_state or st.session_state.get("last_loaded_path") != current_session["path"]:
            records, original_data = session_manager.load_records(current_session["path"], use_cloud)
            st.session_state.records = records
            st.session_state.original_data = original_data
            st.session_state.last_loaded_path = current_session["path"]

    records = st.session_state.get("records", [])

    # ── Determine Step ──
    review_count = sum(1 for r in records if r.needs_review or r.missing_fields)
    confirmed_count = sum(1 for r in records if r.is_confirmed)
    current_step = 1
    if records:
        current_step = 2
    if records and review_count == 0 and confirmed_count > 0:
        current_step = 3

    # ── Step Indicator ──
    st.markdown(render_step_indicator(current_step), unsafe_allow_html=True)

    # ── 3-Tab Layout ──
    tab_upload, tab_edit, tab_export = st.tabs(["📤 アップロード", "✏️ 確認・編集", "📥 CSV出力"])

    # ━━━ Tab 1: Upload ━━━
    with tab_upload:
        col_up, col_info = st.columns([2, 1])
        with col_up:
            st.markdown('<div class="section-title">ファイルアップロード</div>', unsafe_allow_html=True)
            uploaded_files = st.file_uploader(
                "画像を選択 (PNG, JPG, HEIC)",
                type=["png", "jpg", "jpeg", "heic", "heif"],
                accept_multiple_files=True,
                label_visibility="collapsed"
            )
            if uploaded_files and st.button("📤 Inboxへ保存", type="primary"):
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
                        client.put_object(
                            Bucket=get_bucket_name(), Key=f"inbox/{fname}",
                            Body=file_bytes, ContentType="image/jpeg"
                        )
                    else:
                        session_manager.INPUT_DIR.mkdir(parents=True, exist_ok=True)
                        save_path = session_manager.INPUT_DIR / fname
                        with open(save_path, "wb") as f:
                            f.write(file_bytes)
                        convert_heic_to_jpg(save_path)
                    count += 1
                st.success(f"✅ {count}枚を保存しました")
                st.rerun()

        with col_info:
            st.markdown('<div class="section-title">解析</div>', unsafe_allow_html=True)
            # Inbox内の画像ファイル一覧
            inbox_files = []
            if session_manager.INPUT_DIR.exists():
                inbox_files = [
                    f for f in session_manager.INPUT_DIR.glob("*")
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".heic", ".heif")
                ]
            if inbox_files:
                st.caption(f"📁 Inbox: {len(inbox_files)}枚")
            
            if st.button("⚡ Inbox画像を解析", use_container_width=True, type="primary"):
                if not inbox_files:
                    st.warning("Inboxに画像がありません。先にアップロードしてください。")
                else:
                    status_container = st.status("AIがレシートを解析中...", expanded=True)
                    records = session_manager.load_records()
                    success_count = 0
                    error_count = 0
                    done_dir = session_manager.INPUT_DIR.parent / "done"
                    done_dir.mkdir(parents=True, exist_ok=True)
                    
                    for img_file in inbox_files:
                        try:
                            status_container.write(f"🔍 {img_file.name} を解析中...")
                            new_recs = analyze_receipt_image(str(img_file), use_split_scan=False)
                            if new_recs:
                                for rec in new_recs:
                                    rec.source_file = img_file.name
                                    records.append(rec)
                                success_count += len(new_recs)
                            # 処理済みをdoneフォルダへ移動
                            img_file.rename(done_dir / img_file.name)
                        except Exception as e:
                            status_container.write(f"❌ {img_file.name}: {e}")
                            error_count += 1
                    
                    session_manager.save_records(records)
                    status_container.update(
                        label=f"✅ 完了: {success_count}件解析 / {error_count}件エラー",
                        state="complete"
                    )
                    st.rerun()

    # ━━━ Tab 2: Edit ━━━
    with tab_edit:
        if not records:
            st.markdown(
                render_empty_state("📋", "レシートデータなし", "画像をアップロードして解析してください"),
                unsafe_allow_html=True
            )
            return

        # Stats bar
        st.markdown(
            render_stats_bar(len(records), confirmed_count, review_count),
            unsafe_allow_html=True
        )

        # Master table
        st.markdown('<div class="section-title">一覧</div>', unsafe_allow_html=True)
        df_data = []
        for i, r in enumerate(records):
            if r.is_discarded:
                continue
            df_data.append({
                "No": i + 1,
                "日付": r.date,
                "店名": r.vendor,
                "金額": f"¥{r.total_amount:,}",
                "区分": CAT_MAP.get(r.category, ""),
                "状態": status_emoji(get_status(r))
            })
        st.dataframe(pd.DataFrame(df_data), use_container_width=True, hide_index=True)

        # Editor
        st.markdown('<div class="section-title">編集</div>', unsafe_allow_html=True)
        selected_idx = st.number_input(
            "編集する No を選択",
            min_value=1, max_value=len(records), value=1
        ) - 1
        rec = records[selected_idx]

        c1, c2 = st.columns([1, 1], gap="large")

        with c1:
            if rec.image_path:
                render_zoomable_image(rec.image_path)
            else:
                st.markdown(
                    render_empty_state("🖼", "画像なし", ""),
                    unsafe_allow_html=True
                )

        with c2:
            with st.form(key=f"edit_form_{selected_idx}"):
                new_date = st.text_input("日付 (YYYY-MM-DD)", value=rec.date)
                new_vendor = st.text_input("店名", value=rec.vendor)

                fc1, fc2 = st.columns(2)
                with fc1:
                    new_amount = st.number_input("合計金額", value=rec.total_amount)
                with fc2:
                    new_subject = st.text_input("但し書き", value=rec.subject)

                cat_options = list(Category)
                current_cat = rec.category if rec.category in cat_options else Category.UNKNOWN
                new_cat = st.selectbox(
                    "経費区分", options=cat_options,
                    index=cat_options.index(current_cat),
                    format_func=lambda x: CAT_MAP.get(x, x.value)
                )

                fc3, fc4 = st.columns(2)
                with fc3:
                    pay_options = list(PaymentMethod)
                    current_pay = rec.payment_method if rec.payment_method in pay_options else PaymentMethod.UNKNOWN
                    new_pay = st.selectbox(
                        "支払方法", options=pay_options,
                        index=pay_options.index(current_pay),
                        format_func=lambda x: PAY_MAP.get(x, x.value)
                    )
                with fc4:
                    tax_options = list(TaxRate)
                    current_tax = rec.tax_rate_detected if rec.tax_rate_detected in tax_options else TaxRate.UNKNOWN
                    new_tax = st.selectbox(
                        "税区分", options=tax_options,
                        index=tax_options.index(current_tax),
                        format_func=lambda x: TAX_MAP.get(x, x.value)
                    )

                new_invoice = st.text_input("インボイス番号 (T+13桁)", value=rec.invoice_no_norm)

                st.markdown("---")
                is_confirmed = st.checkbox("✅ 確認完了", value=rec.is_confirmed)

                if st.form_submit_button("💾 保存", type="primary", use_container_width=True):
                    rec.date = new_date
                    rec.vendor = new_vendor
                    rec.total_amount = int(new_amount)
                    rec.subject = new_subject
                    rec.category = new_cat
                    rec.payment_method = new_pay
                    rec.tax_rate_detected = new_tax
                    rec.invoice_no_norm = new_invoice
                    rec.is_confirmed = is_confirmed
                    if is_confirmed:
                        rec.needs_review = False
                        if "invoice_no_candidate" in rec.missing_fields:
                            rec.missing_fields.remove("invoice_no_candidate")

                    session_manager.save_records(
                        st.session_state.summary_path,
                        records,
                        st.session_state.original_data,
                        use_cloud
                    )
                    st.success("保存しました")
                    st.rerun()

    # ━━━ Tab 3: Export ━━━
    with tab_export:
        csv_result = generate_csv_data(records)
        valid_rows = csv_result.get("valid", [])
        invalid_rows = csv_result.get("invalid", [])

        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.markdown(f"""
            <div class="modern-card" style="text-align:center; padding:2rem;">
                <div style="font-size:40px; margin-bottom:8px">📄</div>
                <div style="font-size:28px; font-weight:700; color:#202124">{len(valid_rows)}件</div>
                <div style="font-size:12px; color:#80868b; margin-top:4px">出力可能レコード</div>
            </div>
            """, unsafe_allow_html=True)

        with col_b:
            if valid_rows:
                import io
                df_csv = pd.DataFrame(valid_rows)
                csv_bytes = df_csv.to_csv(index=False).encode("utf-8-sig")

                st.markdown("<div style='padding-top:1.5rem'></div>", unsafe_allow_html=True)
                st.download_button(
                    "📥 CSVダウンロード (freee形式)",
                    data=csv_bytes,
                    file_name="receipts.csv",
                    mime="text/csv",
                    use_container_width=True,
                    type="primary"
                )

                if invalid_rows:
                    st.warning(f"⚠️ {len(invalid_rows)}件のレコードに不備があります")
            else:
                st.info("確認済みレシートがありません。「確認・編集」タブで確認を完了してください。")

        if invalid_rows:
            with st.expander(f"不備のあるレコード ({len(invalid_rows)}件)"):
                for row in invalid_rows:
                    st.caption(f"• {row.get('摘要', '不明')} — 不足: {', '.join(row.get('_error_reasons', []))}")

        if valid_rows:
            st.markdown('<div class="section-title">プレビュー</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(valid_rows), use_container_width=True, hide_index=True)
