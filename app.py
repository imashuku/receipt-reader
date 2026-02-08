"""
レシート編集UI (Streamlit)
app.py: エントリーポイント。デバイス判定とルーティングを担当。
"""
import streamlit as st
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from streamlit_javascript import st_javascript

# ─────────────────────────────────────────────
# Step 1: 環境セットアップ (Streamlit Cloud対応)
# ─────────────────────────────────────────────
_project_root = str(Path(__file__).resolve().parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

load_dotenv()

# st.secrets転写
try:
    for key in st.secrets:
        if isinstance(st.secrets[key], str) and key not in os.environ:
            os.environ[key] = st.secrets[key]
except Exception:
    pass

USE_CLOUD_BACKEND = os.environ.get("USE_CLOUD_BACKEND", "false").lower() == "true"

# ─────────────────────────────────────────────
# Step 2: Logicモジュールのロード
# ─────────────────────────────────────────────
# Streamlit CloudでのImport Error回避のため、必要に応じてdirect_loadするが、
# 今回はlogicパッケージがsys.pathにあるため通常importを試みる。
# 失敗した場合のみbypassロジックを使う構造にするのが安全だが、
# 既存の構造を維持して確実にロードする。

import importlib.util

_logic_dir = Path(__file__).resolve().parent / "logic"

def _ensure_logic_loaded():
    """logicパッケージが正しくロードされているか確認"""
    if "logic" not in sys.modules:
        # package init
        spec = importlib.util.spec_from_file_location("logic", str(_logic_dir / "__init__.py"), submodule_search_locations=[str(_logic_dir)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["logic"] = mod
        spec.loader.exec_module(mod)

try:
    _ensure_logic_loaded()
    from logic import models, dummy_data # dummy_data is optional
except ImportError:
    pass # 続行

if USE_CLOUD_BACKEND:
    try:
        from logic import data_layer
    except Exception as e:
        st.warning(f"Cloud backend load failed: {e}")
        USE_CLOUD_BACKEND = False

# ─────────────────────────────────────────────
# Step 3: UI Routing
# ─────────────────────────────────────────────
st.set_page_config(page_title="レシートリーダー", layout="wide", page_icon="🧾")

# Session State Init
if "user_mode" not in st.session_state:
    st.session_state.user_mode = None

def get_device_type():
    # キャッシュキーを変えないとリロードループする可能性があるが、
    # st_javascriptはkeyが変わると再実行される。
    # ここでは一度だけ実行してsession_stateに保存したい。
    
    if st.session_state.user_mode:
        return st.session_state.user_mode

    # JavaScriptで幅を取得
    # keyを固定すると値が更新されないが、初回判定用なのでOK
    ui_width = st_javascript("window.innerWidth", key="device_width_check")
    
    if ui_width is None:
        return "desktop" # 取得できるまではデフォルトPC
    
    if ui_width < 768:
        return "mobile"
    else:
        return "desktop"

# 判定実行
detected_mode = get_device_type()

# ユーザーが手動で切り替えている場合はそちらを優先（st.session_state.user_mode）
current_mode = st.session_state.user_mode or detected_mode

# UIモジュールのインポート（ここで呼ぶことで、app.pyの初期化完了後に実行される）
from ui.mobile import render_mobile
from ui.desktop import render_desktop

if current_mode == "mobile":
    render_mobile(USE_CLOUD_BACKEND)
else:
    render_desktop(USE_CLOUD_BACKEND)

# Footer / Debug
# st.sidebar.caption(f"Mode: {current_mode} (Width: {st.session_state.get('device_width_check')})")
