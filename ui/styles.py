"""
Modern minimal CSS styles for the receipt reader app.
"""

MODERN_CSS = """
<style>
/* ── Global Reset & Theme ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

.stApp {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* Hide default Streamlit hamburger & footer */
#MainMenu, footer, header {visibility: hidden;}

/* ── Step Indicator ── */
.step-indicator {
    display: flex;
    justify-content: center;
    gap: 0;
    margin: 1.5rem auto 2rem;
    max-width: 500px;
}
.step-item {
    flex: 1;
    text-align: center;
    position: relative;
    padding: 0.5rem 0;
}
.step-item::after {
    content: '';
    position: absolute;
    top: 22px;
    right: -50%;
    width: 100%;
    height: 2px;
    background: #e0e0e0;
    z-index: 0;
}
.step-item:last-child::after { display: none; }
.step-item.active::after { background: #4a90d9; }

.step-number {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: #e8eaed;
    color: #5f6368;
    font-weight: 600;
    font-size: 14px;
    position: relative;
    z-index: 1;
    margin: 0 auto;
    transition: all 0.3s ease;
}
.step-item.active .step-number {
    background: #4a90d9;
    color: white;
    box-shadow: 0 2px 8px rgba(74, 144, 217, 0.3);
}
.step-item.done .step-number {
    background: #34a853;
    color: white;
}
.step-label {
    display: block;
    margin-top: 6px;
    font-size: 11px;
    color: #80868b;
    font-weight: 500;
    letter-spacing: 0.02em;
}
.step-item.active .step-label { color: #4a90d9; font-weight: 600; }
.step-item.done .step-label { color: #34a853; }

/* ── Card Component ── */
.modern-card {
    background: white;
    border: 1px solid #f0f0f0;
    border-radius: 12px;
    padding: 1.25rem;
    margin-bottom: 0.75rem;
    transition: box-shadow 0.2s ease, transform 0.15s ease;
}
.modern-card:hover {
    box-shadow: 0 4px 12px rgba(0,0,0,0.06);
    transform: translateY(-1px);
}

/* Status variants */
.modern-card.status-valid {
    border-left: 4px solid #34a853;
}
.modern-card.status-review {
    border-left: 4px solid #fbbc04;
}
.modern-card.status-invalid {
    border-left: 4px solid #ea4335;
}

/* ── Receipt Card Content ── */
.receipt-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 0.5rem;
}
.receipt-vendor {
    font-size: 15px;
    font-weight: 600;
    color: #202124;
    margin: 0;
}
.receipt-amount {
    font-size: 18px;
    font-weight: 700;
    color: #202124;
    white-space: nowrap;
}
.receipt-meta {
    display: flex;
    gap: 12px;
    font-size: 12px;
    color: #80868b;
    margin-top: 4px;
}
.receipt-meta span {
    display: inline-flex;
    align-items: center;
    gap: 3px;
}
.receipt-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
}
.badge-valid { background: #e6f4ea; color: #1e8e3e; }
.badge-review { background: #fef7e0; color: #b06000; }
.badge-invalid { background: #fce8e6; color: #c5221f; }

/* ── Upload Area ── */
.upload-zone {
    border: 2px dashed #dadce0;
    border-radius: 16px;
    padding: 2.5rem 1.5rem;
    text-align: center;
    background: #fafbfc;
    transition: all 0.2s ease;
    margin-bottom: 1rem;
}
.upload-zone:hover {
    border-color: #4a90d9;
    background: #f0f6ff;
}
.upload-icon {
    font-size: 40px;
    margin-bottom: 8px;
}
.upload-text {
    color: #5f6368;
    font-size: 14px;
    margin: 0;
}
.upload-hint {
    color: #80868b;
    font-size: 12px;
    margin-top: 4px;
}

/* ── Stats Bar ── */
.stats-bar {
    display: flex;
    gap: 1rem;
    margin-bottom: 1.5rem;
}
.stat-item {
    flex: 1;
    background: white;
    border: 1px solid #f0f0f0;
    border-radius: 12px;
    padding: 1rem;
    text-align: center;
}
.stat-value {
    font-size: 24px;
    font-weight: 700;
    color: #202124;
    line-height: 1;
}
.stat-label {
    font-size: 11px;
    color: #80868b;
    margin-top: 4px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* ── Section Title ── */
.section-title {
    font-size: 13px;
    font-weight: 600;
    color: #80868b;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 1.5rem 0 0.75rem;
}

/* ── Streamlit Overrides ── */
/* Buttons */
.stButton > button {
    border-radius: 8px !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    padding: 0.5rem 1.25rem !important;
    transition: all 0.2s ease !important;
}
.stButton > button[kind="primary"] {
    background: #4a90d9 !important;
    border: none !important;
}
.stButton > button[kind="primary"]:hover {
    background: #3a7bc8 !important;
    box-shadow: 0 2px 8px rgba(74, 144, 217, 0.3) !important;
}

/* Download button */
.stDownloadButton > button {
    border-radius: 8px !important;
    font-weight: 500 !important;
}

/* File uploader */
[data-testid="stFileUploader"] {
    border-radius: 12px !important;
}
[data-testid="stFileUploader"] section {
    border-radius: 12px !important;
    border: 2px dashed #dadce0 !important;
    padding: 1.5rem !important;
}

/* Expander */
.streamlit-expanderHeader {
    font-weight: 500 !important;
    font-size: 14px !important;
    border-radius: 8px !important;
}

/* DataFrame */
[data-testid="stDataFrame"] {
    border-radius: 12px !important;
    overflow: hidden;
}

/* Form */
[data-testid="stForm"] {
    border: 1px solid #f0f0f0 !important;
    border-radius: 12px !important;
    padding: 1.5rem !important;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: #fafbfc !important;
}
[data-testid="stSidebar"] .stMarkdown h2 {
    font-size: 16px !important;
    font-weight: 600 !important;
    color: #202124;
}

/* Number input */
.stNumberInput > div > div > input {
    border-radius: 8px !important;
}

/* Text input */
.stTextInput > div > div > input {
    border-radius: 8px !important;
}

/* ── Mobile Specific ── */
@media (max-width: 768px) {
    .modern-card { padding: 1rem; }
    .stats-bar { flex-direction: column; gap: 0.5rem; }
    .stat-item { padding: 0.75rem; }
    .stat-value { font-size: 20px; }
    .step-indicator { margin: 1rem auto 1.5rem; }
    .receipt-amount { font-size: 16px; }
}

/* ── Empty State ── */
.empty-state {
    text-align: center;
    padding: 3rem 1.5rem;
    color: #80868b;
}
.empty-state-icon {
    font-size: 48px;
    margin-bottom: 12px;
}
.empty-state-title {
    font-size: 16px;
    font-weight: 600;
    color: #5f6368;
    margin-bottom: 4px;
}
.empty-state-text {
    font-size: 13px;
}
</style>
"""


def render_step_indicator(current_step: int) -> str:
    """Render a 3-step progress indicator. Steps: 1=Upload, 2=Confirm, 3=Export"""
    steps = [
        ("1", "アップロード"),
        ("2", "確認・編集"),
        ("3", "CSV出力"),
    ]
    html = '<div class="step-indicator">'
    for i, (num, label) in enumerate(steps):
        step_num = i + 1
        cls = "step-item"
        if step_num < current_step:
            cls += " done"
        elif step_num == current_step:
            cls += " active"
        
        icon = "✓" if step_num < current_step else num
        html += f'<div class="{cls}"><div class="step-number">{icon}</div><span class="step-label">{label}</span></div>'
    html += '</div>'
    return html


def render_stats_bar(total: int, confirmed: int, review: int) -> str:
    """Render summary stats as cards"""
    return f"""
    <div class="stats-bar">
        <div class="stat-item">
            <div class="stat-value">{total}</div>
            <div class="stat-label">合計</div>
        </div>
        <div class="stat-item">
            <div class="stat-value" style="color:#34a853">{confirmed}</div>
            <div class="stat-label">確認済み</div>
        </div>
        <div class="stat-item">
            <div class="stat-value" style="color:#fbbc04">{review}</div>
            <div class="stat-label">要確認</div>
        </div>
    </div>
    """


def render_receipt_card(vendor: str, date: str, amount: int, status: str, category: str) -> str:
    """Render a receipt as a modern card"""
    status_class = {"valid": "status-valid", "needs_review": "status-review", "invalid": "status-invalid"}.get(status, "")
    badge_class = {"valid": "badge-valid", "needs_review": "badge-review", "invalid": "badge-invalid"}.get(status, "")
    badge_text = {"valid": "確認済み", "needs_review": "要確認", "invalid": "不備あり"}.get(status, "")
    
    return f"""
    <div class="modern-card {status_class}">
        <div class="receipt-header">
            <div>
                <p class="receipt-vendor">{vendor or '不明な店舗'}</p>
                <div class="receipt-meta">
                    <span>📅 {date}</span>
                    <span>🏷 {category}</span>
                </div>
            </div>
            <div style="text-align:right">
                <div class="receipt-amount">¥{amount:,}</div>
                <span class="receipt-badge {badge_class}">{badge_text}</span>
            </div>
        </div>
    </div>
    """


def render_empty_state(icon: str, title: str, text: str) -> str:
    return f"""
    <div class="empty-state">
        <div class="empty-state-icon">{icon}</div>
        <div class="empty-state-title">{title}</div>
        <div class="empty-state-text">{text}</div>
    </div>
    """
