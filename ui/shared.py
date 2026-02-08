import streamlit as st
import streamlit.components.v1 as components
import base64
import io
import socket
from pathlib import Path
import subprocess
from PIL import Image as PILImage, ImageOps

def get_status(rec) -> str:
    """ステータスラベルを返す"""
    if not rec.missing_fields and not rec.needs_review:
        return "valid"
    elif rec.needs_review:
        return "needs_review"
    else:
        return "invalid"


def status_emoji(status: str) -> str:
    return {"valid": "✅", "needs_review": "⚠️", "invalid": "❌"}.get(status, "❓")


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def convert_heic_to_jpg(input_path: Path) -> Path:
    """
    HEIC/HEIFをJPEGに変換する (macOS sips利用)。
    変換成功なら新しいパスを返す。失敗なら元のパスを返す。
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


def render_zoomable_image(img_path: str):
    """
    パン＆ズーム画像ビューア。
    - ホイール: ズームイン/アウト
    - ドラッグ: パン
    - ダブルクリック: リセット
    - クラウドURL対応
    - iPhone EXIF回転対応
    """
    
    # URLの場合はrequestsで取得、ローカルファイルの場合は直接読み込み
    if img_path.startswith("http://") or img_path.startswith("https://"):
        import requests
        try:
            response = requests.get(img_path, timeout=10)
            response.raise_for_status()
            img_data = response.content
            
            # EXIF回転を適用してからbase64化
            with PILImage.open(io.BytesIO(img_data)) as pil_img:
                pil_img = ImageOps.exif_transpose(pil_img)
                w, h = pil_img.size
                display_h = min(int(600 * h / w), 760)
                # 回転済み画像をbase64化
                buf = io.BytesIO()
                fmt = pil_img.format or "JPEG"
                pil_img.save(buf, format=fmt)
                img_b64 = base64.b64encode(buf.getvalue()).decode()
            
            # MIMEタイプ
            content_type = response.headers.get("Content-Type", "image/jpeg")
            mime = content_type.split(";")[0].strip()
        except Exception as e:
            st.error(f"画像の読み込みに失敗しました: {e}")
            return
    else:
        # ローカルファイル: EXIF回転を適用
        try:
            # Check if file exists (only for local paths)
            if not Path(img_path).exists():
                 st.error(f"画像が見つかりません: {img_path}")
                 return

            with PILImage.open(img_path) as pil_img:
                pil_img = ImageOps.exif_transpose(pil_img)
                w, h = pil_img.size
                display_h = min(int(600 * h / w), 760)
                # 回転済み画像をbase64化
                buf = io.BytesIO()
                fmt = pil_img.format or "JPEG"
                pil_img.save(buf, format=fmt)
                img_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            # フォールバック: そのまま読み込み
            if Path(img_path).exists():
                with open(img_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode()
            else:
                st.error("画像読み込みエラー")
                return
            display_h = 650

        ext = Path(img_path).suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp"}.get(ext, "image/jpeg")
    
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

      /* ホイールズーム */
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
