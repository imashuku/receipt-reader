"""
Gemini / OpenAI デュアルバックエンド対応 OCRクライアント
- Gemini 2.0 Flash をデフォルトで使用
- クォータ制限等で失敗した場合、OpenAI GPT-4o にフォールバック
- T番号: ラベル近傍優先抽出 + ハイフン付き対応
- 日付: 候補スコアリング (現在年±2優先)
"""
import os
import json
import re
import base64
import unicodedata
from datetime import datetime
from typing import List, Optional, Tuple
from PIL import Image
import io
import time
import random
import functools
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .models import ReceiptRecord, TaxRate, PaymentMethod, Category

load_dotenv()

# モデル設定
GEMINI_MODEL = "gemini-2.0-flash"
OPENAI_MODEL = "gpt-4o"

# 現在年 (日付スコアリング用)
CURRENT_YEAR = datetime.now().year  # 2026

# ── 共通の抽出プロンプト ──
EXTRACTION_PROMPT = """
あなたはレシート・領収書の情報を正確に抽出するOCRエキスパートです。

この画像に含まれる**最も主要な1枚のレシート/領収書**を検出し、
以下の情報をJSON配列で返してください。

【重要：1枚撮りモード】
- 画像内には**必ず1枚**のレシートがあると仮定してください。
- 背景に他の書類や物体が写っていても、**中央にある最も明確なレシート1枚のみ**を抽出対象としてください。
- 決して複数のレシートとして分割したり、ノイズを別のレシートとして誤検知しないでください。

各レシートに対して以下のフィールドを抽出：
{
  "date": "YYYY/MM/DD形式の日付",
  "vendor": "支払先/店名",
  "subject": "件名・品目の要約（例: タクシー代、昼食代、会費など）",
  "total_amount": 税込総額（整数）,
  "invoice_no_raw": "T番号（インボイス登録番号）があればそのまま記載。なければ空文字",
  "tax_rate": "10" or "8" or "8_reduced" or "unknown",
  "payment_clues": "支払方法の手がかり（お預り/お釣り→cash, PayPay→paypay, カード→credit, 不明→unknown）",
  "ocr_full_text": "レシート全文テキスト（改行は\\nで区切る）",
  "box_2d": [ymin, xmin, ymax, xmax]  // レシートの正規化座標 (0-1000)
}

抽出ルール:
1. invoice_no_raw: 「登録番号」「適格請求書発行事業者」の近くにある T+数字列をそのまま記載
2. total_amount: 「計/合計/領収金額/乗車料金/お支払い」の近くの金額を優先
3. tax_rate: 「10%/8%/8.0%/10.0%」表記を探す。軽減税率(※)マークがあれば "8_reduced"。非課税/不課税/対象外の記載があれば "exempt"
4. payment_clues: 「お預り/お釣り/釣銭」→cash、「PayPay」→paypay、「VISA/Master/JCB/カード」→credit
5. ocr_full_text: レシート全文を省略せずすべて記載すること（T番号抽出の後処理に使用）
6. box_2d: 画像全体を1000x1000とした時の、そのレシートのバウンディングボックス [ymin, xmin, ymax, xmax]

**必ずJSON配列のみを返してください。マークダウンのコードブロックは不要です。**
レシートが1枚でも配列 [...] で返してください。
"""


def _is_retryable_error(e: Exception) -> bool:
    """
    リトライすべきエラーかどうかを判定
    - 429 (Rate Limit)
    - Quota Exceeded / Resource Exhausted
    """
    err_str = str(e).lower()
    return "429" in err_str or "quota" in err_str or "rate limit" in err_str or "resource exhausted" in err_str


# Common Retry Configuration
# 1回目: 2s, 2回目: 4s, 3回目: 8s (approx)
RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    retry=retry_if_exception(_is_retryable_error),
    reraise=True
)


def _image_to_base64(image_path: str) -> str:
    """画像をbase64文字列に変換"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


import tempfile

def _split_image(image_path: str) -> List[Tuple[str, tuple]]:
    """
    画像を 2x2（4分割）+ 中央クロップ（1枚）の計5枚に分割して一時保存。
    戻り値: [(一時ファイルパス, (offset_y, offset_x, original_h, original_w)), ...]
    """
    img = Image.open(image_path)
    w, h = img.size
    
    # 4分割
    half_w, half_h = w // 2, h // 2
    crops = [
        (0, 0, half_w, half_h),           # 左上
        (half_w, 0, w, half_h),           # 右上
        (0, half_h, half_w, h),           # 左下
        (half_w, half_h, w, h),           # 右下
    ]
    
    # 中央クロップ (1/2サイズ)
    center_w, center_h = w // 2, h // 2
    center_x = (w - center_w) // 2
    center_y = (h - center_h) // 2
    crops.append((center_x, center_y, center_x + center_w, center_y + center_h))
    
    results = []
    # システムの一時ディレクトリを使用 (Streamlitの再読み込みループ回避)
    temp_dir = tempfile.mkdtemp()
    filename = os.path.splitext(os.path.basename(image_path))[0]
    
    for i, (x1, y1, x2, y2) in enumerate(crops):
        crop_img = img.crop((x1, y1, x2, y2))
        temp_path = os.path.join(temp_dir, f"{filename}_crop_{i}.jpg")
        crop_img.save(temp_path, "JPEG")
        
        # 正規化座標を復元するためのオフセット情報を保存
        # (offset_y, offset_x, crop_h, crop_w, original_h, original_w)
        results.append((temp_path, (y1, x1, y2-y1, x2-x1, h, w)))
        
    return results


# ═══════════════════════════════════════════════════════════
#  T番号（インボイス番号）抽出ロジック
# ═══════════════════════════════════════════════════════════

# T番号探索のトリガーキーワード
_INVOICE_LABEL_KEYWORDS = ["登録番号", "適格請求書発行事業者", "適格請求書", "インボイス"]

# T番号候補の正規表現 (広め)
_T_NUMBER_PATTERN = r"[TＴ][0-9０-９\-ー－−\–\—\‐\s]{10,}"


def _zen_to_han(text: str) -> str:
    """全角英数字・記号を半角に変換 (NFKC正規化)"""
    return unicodedata.normalize("NFKC", text)


def _normalize_invoice_candidate(raw: str) -> Tuple[str, bool]:
    """
    T番号候補文字列を正規化:
      1. 全角→半角変換
      2. ハイフン/長音/ダッシュ系すべて除去
      3. スペース除去
      4. 桁数チェック:
         - 13桁 → そのまま確定 (高信頼度)
         - 12桁 → 先頭0補完して13桁に (低信頼度)
         - 14桁 → 先頭が0なら除去して13桁に (低信頼度)
         - それ以外 → 不採用

    戻り値: (正規化済みT番号, 低信頼度フラグ)
    """
    if not raw:
        return "", False
    # Step 1: 全角→半角
    s = _zen_to_han(raw)
    # Step 2: ハイフン・長音・ダッシュ系の除去
    s = re.sub(r"[\-−ー–—‐‑‒―⁃₋﹣－]", "", s)
    # Step 3: スペース・改行除去
    s = re.sub(r"[\s\u3000]", "", s).strip()
    # 先頭が T であることを確認
    if not s.startswith("T"):
        return "", False
    digits = s[1:]  # T以降
    # 数字のみ抽出 (OCRノイズで記号が混入する場合)
    digits = re.sub(r"[^0-9]", "", digits)

    if len(digits) == 13:
        # 完全一致 → 高信頼度
        return f"T{digits}", False
    elif len(digits) == 12:
        # 1桁欠落 → 先頭0補完 (低信頼度)
        padded = f"T0{digits}"
        return padded, True
    elif len(digits) == 14 and digits.startswith("0"):
        # 先頭0余分 → 除去 (低信頼度)
        trimmed = f"T{digits[1:]}"
        return trimmed, True
    else:
        return "", False


def _extract_invoice_no_from_text(ocr_text: str, ai_raw: str = "") -> Tuple[str, str, bool]:
    """
    T番号をラベル近傍優先で抽出する。

    戦略:
      1. AI が返した invoice_no_raw をまず正規化
      2. OCR全文中の「登録番号」等のラベル近傍 (前後60文字) から候補を探索
      3. ラベル近傍で見つからない場合のみ全文検索するが confidence を下げる

    桁数ロジック:
      - 13桁 → そのまま確定 (高信頼度)
      - 12桁 → 先頭0補完 (低信頼度)
      - 14桁先頭0 → 除去 (低信頼度)

    戻り値: (確定T番号, デバッグ情報, 低信頼度フラグ)
    """
    debug_info = ""

    # (1) AI が返した値を正規化して試す
    if ai_raw:
        norm, norm_low = _normalize_invoice_candidate(ai_raw)
        if norm:
            suffix = " (桁数補完あり・要確認)" if norm_low else ""
            debug_info = f"AI提供値から確定: {ai_raw} → {norm}{suffix}"
            return norm, debug_info, norm_low
        debug_info += f"AI値 '{ai_raw}' 不適合; "

    # OCRテキストがなければ終了
    if not ocr_text:
        return "", debug_info + "OCRテキストなし", False

    # (2) ラベル近傍検索 (高信頼度ソース)
    label_windows = []
    for kw in _INVOICE_LABEL_KEYWORDS:
        for match in re.finditer(re.escape(kw), ocr_text):
            start = max(0, match.start() - 60)
            end = min(len(ocr_text), match.end() + 60)
            window = ocr_text[start:end]
            label_windows.append((kw, window))

    if label_windows:
        debug_info += f"ラベル {len(label_windows)} 箇所検出; "
        for kw, window in label_windows:
            candidates = re.findall(_T_NUMBER_PATTERN, window)
            for cand in candidates:
                norm, norm_low = _normalize_invoice_candidate(cand)
                if norm:
                    suffix = " (桁数補完・要確認)" if norm_low else ""
                    debug_info += f"ラベル近傍'{kw}'から確定: {cand.strip()} → {norm}{suffix}"
                    return norm, debug_info, norm_low

            # ── フォールバック: 数字ブロック結合 ──
            # 正規表現で一発で見つからない場合 (例: "T 123 456" のようにスペース過多など)
            # アルファベットT と 数字ブロックを拾って結合してみる
            tokens = re.findall(r"[TＴt]|[0-9０-９]+", window)
            for i, token in enumerate(tokens):
                # Tで始まるトークン、またはTそのもの
                if not re.match(r"^[TＴt]", token):
                    continue
                
                # ここから後ろのトークンを順に結合してテスト (最大8トークン先まで)
                combined = token
                # 結合に使用した文字数(数字部分)のカウント
                digit_count = len(re.sub(r"[^0-9]", "", combined))
                
                for j in range(i + 1, min(len(tokens), i + 8)):
                    combined += tokens[j]
                    digit_count += len(re.sub(r"[^0-9]", "", tokens[j]))
                    
                    # 正規化してチェック (12〜14桁ならヒットする可能性あり)
                    if 12 <= digit_count <= 14:
                        norm, norm_low = _normalize_invoice_candidate(combined)
                        if norm:
                            debug_info += f"ラベル近傍'{kw}'ブロック結合から検出: {combined} → {norm} (candidate扱い)"
                            return norm, debug_info, True  # 結合ロジックは常にcandidate扱い
                    
                    # 14桁を超えたら打ち切り
                    if digit_count > 14:
                        break

    # (3) 全文検索 (低信頼度 → needs_review=true)
    all_candidates = re.findall(_T_NUMBER_PATTERN, ocr_text)
    if all_candidates:
        debug_info += f"全文候補{len(all_candidates)}件: {[c.strip() for c in all_candidates]}; "
        for cand in all_candidates:
            norm, norm_low = _normalize_invoice_candidate(cand)
            if norm:
                debug_info += f"全文検索から検出(要確認): {cand.strip()} → {norm}"
                return norm, debug_info, True  # 全文検索 = 常に低信頼度

    debug_info += "候補なし"
    return "", debug_info, False


# ═══════════════════════════════════════════════════════════
#  日付抽出ロジック (候補スコアリング)
# ═══════════════════════════════════════════════════════════

# 日付ラベルキーワード
_DATE_LABEL_KEYWORDS = ["日付", "利用日", "乗車日", "発行日", "領収日", "年月日"]


def _extract_best_date(ocr_text: str, ai_date: str = "") -> Tuple[str, str, bool]:
    """
    OCR全文テキストから日付候補を抽出し、スコアリングで最適な日付を決定。

    スコアリング:
      - 日付ラベル近傍 (前30文字以内)  → +10
      - 現在年 ±2 以内               → +5
      - 現在年 ±2 超え               → -5

    戻り値: (最適日付, デバッグ情報, needs_review)
    """
    if not ocr_text:
        # OCRテキストなし → AI返却値をそのまま使用
        return ai_date, "OCRテキストなし→AI値使用", not bool(ai_date)

    debug_info = ""

    # 日付候補パターン (複数形式に対応)
    date_patterns = [
        # YYYY/MM/DD or YYYY-MM-DD
        (r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", "slash"),
        # YYYY年MM月DD日
        (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "kanji"),
        # 令和X年MM月DD日 (令和元年=2019, 令和2年=2020, ...)
        (r"令和\s*(\d{1,2})年(\d{1,2})月(\d{1,2})日", "reiwa"),
        # 令和X年/MM/DD (混在形式)
        (r"令和\s*(\d{1,2})年[/\-](\d{1,2})[/\-](\d{1,2})", "reiwa"),
    ]

    # 日付ラベルの位置をすべて特定
    label_positions = []
    for kw in _DATE_LABEL_KEYWORDS:
        for m in re.finditer(re.escape(kw), ocr_text):
            label_positions.append(m.end())

    candidates = []

    for pattern, fmt in date_patterns:
        for m in re.finditer(pattern, ocr_text):
            if fmt == "reiwa":
                # 令和年 → 西暦変換
                reiwa_year = int(m.group(1))
                year = 2018 + reiwa_year
                month = int(m.group(2))
                day = int(m.group(3))
            else:
                year = int(m.group(1))
                month = int(m.group(2))
                day = int(m.group(3))

            # 基本的な日付バリデーション
            if not (1 <= month <= 12 and 1 <= day <= 31 and 2000 <= year <= 2099):
                continue

            pos = m.start()
            score = 0

            # スコア: 日付ラベル近傍ボーナス (ラベル直後30文字以内)
            for lp in label_positions:
                if 0 <= pos - lp <= 30:
                    score += 10
                    break

            # スコア: 年の妥当性
            if CURRENT_YEAR - 2 <= year <= CURRENT_YEAR + 2:
                score += 5
            else:
                score -= 5

            date_str = f"{year:04d}/{month:02d}/{day:02d}"
            candidates.append((date_str, score, year, pos))

    if not candidates:
        # OCRテキストから日付が見つからない → AI値を使用
        debug_info = f"OCRから日付候補なし→AI値使用: {ai_date}"
        needs_review = False
        # AI値の年チェック
        if ai_date:
            try:
                ai_year = int(ai_date.split("/")[0])
                if not (CURRENT_YEAR - 2 <= ai_year <= CURRENT_YEAR + 2):
                    needs_review = True
                    debug_info += f" (年{ai_year}は範囲外→要確認)"
            except (ValueError, IndexError):
                pass
        return ai_date, debug_info, needs_review

    # スコア降順 → 位置順でソート
    candidates.sort(key=lambda x: (-x[1], x[3]))
    best = candidates[0]

    # 年が範囲外なら needs_review
    needs_review = not (CURRENT_YEAR - 2 <= best[2] <= CURRENT_YEAR + 2)

    debug_info = (
        f"候補{len(candidates)}件; "
        f"ベスト: {best[0]} (score={best[1]})"
    )
    if ai_date and ai_date != best[0]:
        debug_info += f"; AI値 {ai_date} を上書き"

    return best[0], debug_info, needs_review


# ═══════════════════════════════════════════════════════════
#  共通ユーティリティ
# ═══════════════════════════════════════════════════════════

def _map_tax_rate(rate_str: str) -> TaxRate:
    """文字列 → TaxRate Enum"""
    mapping = {
        "10": TaxRate.RATE_10,
        "8": TaxRate.RATE_8,
        "8_reduced": TaxRate.RATE_8_REDUCED,
        "exempt": TaxRate.EXEMPT,
    }
    return mapping.get(rate_str, TaxRate.UNKNOWN)


def _map_payment(clue: str) -> PaymentMethod:
    """手がかり文字列 → PaymentMethod Enum"""
    clue_lower = clue.lower().strip()
    if clue_lower == "cash":
        return PaymentMethod.CASH
    elif clue_lower == "paypay":
        return PaymentMethod.PAYPAY
    elif clue_lower == "credit":
        return PaymentMethod.CREDIT
    return PaymentMethod.UNKNOWN


def _parse_response_text(raw_text: str) -> list:
    """AIレスポンスの生テキストをJSONリストにパース"""
    text = raw_text.strip()

    # マークダウンコードブロックの除去
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)

    try:
        extracted = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] AI応答のJSON解析失敗: {e}")
        print(f"[DEBUG] 生テキスト: {text[:500]}")
        return []

    if not isinstance(extracted, list):
        extracted = [extracted]

    return extracted


# ═══════════════════════════════════════════════════════════
#  APIコール
# ═══════════════════════════════════════════════════════════

def _call_gemini(image_path: str) -> str:
    """Gemini 2.0 Flash で画像を解析 (Retry on 429)"""
    return _call_gemini_impl(image_path)

@RETRY_DECORATOR
def _call_gemini_impl(image_path: str) -> str:
    """Gemini 2.0 Flash で画像を解析"""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    img = Image.open(image_path)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[EXTRACTION_PROMPT, img],
        config=types.GenerateContentConfig(temperature=0.1),
    )
    return response.text


def _call_openai(image_path: str) -> str:
    """OpenAI GPT-4o で画像を解析 (Retry on 429)"""
    return _call_openai_impl(image_path)

@RETRY_DECORATOR
def _call_openai_impl(image_path: str) -> str:
    """OpenAI GPT-4o で画像を解析"""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    b64 = _image_to_base64(image_path)

    # 画像の拡張子からmime type推定
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/png")

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                ],
            }
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content


# ═══════════════════════════════════════════════════════════
#  メイン処理
# ═══════════════════════════════════════════════════════════

def analyze_receipt_image(image_path: str, use_split_scan: bool = False) -> List[ReceiptRecord]:
    """
    画像ファイルパスを受け取り、OCR → ReceiptRecord リストを返す。
    Gemini → 失敗時 OpenAI にフォールバック。

    後処理:
      - T番号: ラベル近傍優先 → 全文検索フォールバック (低信頼度)
      - 日付:  候補スコアリング (現在年±2優先)
      - 座標:  box_2d を region に格納
    """
    if use_split_scan:
        return _analyze_receipt_image_split(image_path)

    records = _analyze_single_image(image_path)
    # 互換性のため AnalysisResult でラップして返す
    return AnalysisResult(records, ["Single scan performed. No merge needed."], raw_records=records)


def _analyze_single_image(image_path: str, offset_info: Optional[tuple] = None) -> List[ReceiptRecord]:
    """単一画像の解析 (オフセット情報があれば座標変換を行う)"""
    raw_text = None
    backend_used = None

    # (1) Gemini を試す
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            print(f"[INFO] Gemini 2.0 Flash で解析を試みます... ({os.path.basename(image_path)})")
            raw_text = _call_gemini(image_path)
            backend_used = "Gemini"
        except Exception as e:
            print(f"[WARN] Gemini 失敗: {e}")
            raw_text = None

    # (2) OpenAI にフォールバック
    if raw_text is None:
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                print("[INFO] OpenAI GPT-4o にフォールバックします...")
                raw_text = _call_openai(image_path)
                backend_used = "gpt-4o"
            except Exception as e:
                print(f"[ERROR] OpenAI も失敗: {e}")
                return []
        else:
            print("[ERROR] 利用可能なAPIキーがありません")
            return []

    # レスポンスをパース
    extracted = _parse_response_text(raw_text)
    if not extracted:
        return []

    # ReceiptRecord に変換 (後処理付き)
    records: List[ReceiptRecord] = []
    for i, item in enumerate(extracted):
        ocr_text = item.get("ocr_full_text", "")
        vendor = item.get("vendor", "?")

        # ── T番号 拡張抽出 (ラベル近傍優先) ──
        ai_raw = item.get("invoice_no_raw", "")
        invoice_norm, invoice_debug, invoice_low_conf = _extract_invoice_no_from_text(ocr_text, ai_raw)
        
        # ── 日付 候補スコアリング ──
        ai_date = item.get("date", "")
        best_date, date_debug, date_needs_review = _extract_best_date(ocr_text, ai_date)
        
        # ── 基本マッピング ──
        tax_rate = _map_tax_rate(item.get("tax_rate", "unknown"))
        payment = _map_payment(item.get("payment_clues", "unknown"))

        # ── needs_review / missing_fields 判定 ──
        needs_review = False
        missing = []

        if not best_date:
            missing.append("date")
            needs_review = True
        if not vendor or vendor == "?":
            missing.append("vendor")
            needs_review = True
        if not item.get("total_amount") or item.get("total_amount", 0) == 0:
            missing.append("total_amount")
            needs_review = True
        if tax_rate == TaxRate.UNKNOWN:
            missing.append("tax_rate")
            needs_review = True
        if payment == PaymentMethod.UNKNOWN:
            missing.append("payment_method")
            needs_review = True

        # 日付年が範囲外 → 要確認
        if date_needs_review:
            needs_review = True
            if "date_year_out_of_range" not in missing:
                missing.append("date_year_out_of_range")

        # T番号が低信頼度で検出された場合
        if invoice_low_conf:
            invoice_confirmed = ""
            invoice_candidate = invoice_norm
            needs_review = True
            if "invoice_no_candidate" not in missing:
                missing.append("invoice_no_candidate")
        else:
            invoice_confirmed = invoice_norm
            invoice_candidate = ""

        # ── 座標 (box_2d) ──
        box_2d = item.get("box_2d", None)
        region = None
        if box_2d and isinstance(box_2d, list) and len(box_2d) == 4:
            try:
                # 0-1000正規化座標
                ymin, xmin, ymax, xmax = [int(v) for v in box_2d]
                
                # オフセット情報がある場合、グローバル座標に変換
                if offset_info:
                    # offset_info: (off_y, off_x, crop_h, crop_w, orig_h, orig_w)
                    off_y, off_x, crop_h, crop_w, orig_h, orig_w = offset_info
                    
                    # ローカル(0-1000) → ピクセル
                    local_y = (ymin / 1000) * crop_h
                    local_x = (xmin / 1000) * crop_w
                    local_h = ((ymax - ymin) / 1000) * crop_h
                    local_w = ((xmax - xmin) / 1000) * crop_w
                    
                    # ピクセル → グローバルピクセル
                    global_y = off_y + local_y
                    global_x = off_x + local_x
                    
                    # グローバルピクセル → グローバル(0-1000)
                    if orig_h > 0 and orig_w > 0:
                        new_ymin = int((global_y / orig_h) * 1000)
                        new_xmin = int((global_x / orig_w) * 1000)
                        new_ymax = int(((global_y + local_h) / orig_h) * 1000)
                        new_xmax = int(((global_x + local_w) / orig_w) * 1000)
                        region = [new_ymin, new_xmin, new_ymax, new_xmax]
                else:
                    region = [ymin, xmin, ymax, xmax]
            except Exception:
                pass

        record = ReceiptRecord(
            date=best_date,
            vendor=vendor,
            subject=item.get("subject", ""),
            total_amount=int(item.get("total_amount", 0)),
            invoice_no_norm=invoice_confirmed,
            invoice_candidate=invoice_candidate,
            qualified_flag="○" if invoice_confirmed else "",
            tax_rate_detected=tax_rate,
            payment_method=payment,
            category=Category.UNKNOWN,
            needs_review=needs_review,
            missing_fields=missing,
            segment_id=f"seg_{i}",
            region=region,
            backend_used=backend_used if backend_used else "Unknown",
        )
        records.append(record)

    return records


import concurrent.futures

def _analyze_receipt_image_split(image_path: str) -> List[ReceiptRecord]:
    """詳細スキャン（4分割+中央）を実行して結果をマージ"""
    print("[INFO] 詳細スキャン(Split Scan)を開始します...")
    
    # 1. 全体スキャン
    print("[INFO] Step 1: 全体スキャン")
    all_records = _analyze_single_image(image_path)
    
    # 2. 分割スキャン (並列実行)
    splits = _split_image(image_path)
    print(f"[INFO] Step 2: 分割スキャン開始 (計{len(splits)}枚, 並列実行)")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_split = {
            executor.submit(_analyze_single_image, path, offset): path
            for path, offset in splits
        }
        
        for future in concurrent.futures.as_completed(future_to_split):
            path = future_to_split[future]
            try:
                records = future.result()
                all_records.extend(records)
                print(f"[INFO] Split scan finished for {os.path.basename(path)}")
            except Exception as e:
                print(f"[ERROR] Split scan failed for {os.path.basename(path)}: {e}")
            finally:
                # ファイル削除
                if os.path.exists(path):
                    os.remove(path)

    # 3. マージ（重複排除）
    return _merge_records(all_records)


import hashlib
from collections import defaultdict

class AnalysisResult(list):
    """リスト互換の解析結果クラス (ログ情報とRawデータを保持)"""
    def __init__(self, iterable=None, logs=None, raw_records=None):
        super().__init__(iterable or [])
        self.logs = logs or []
        self.raw_records = raw_records or []

def _normalize_text(text: str) -> str:
    """ゆらぎ吸収用のテキスト正規化"""
    if not text:
        return ""
    # NFKC正規化 & 小文字化
    norm = unicodedata.normalize("NFKC", text).lower()
    # 英数字と日本語のみ残す (記号除去)
    return re.sub(r"[^0-9a-z\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]", "", norm)

def _fingerprint_text(text: str) -> str:
    """テキストのフィンガープリント(ハッシュ)生成"""
    norm = _normalize_text(text)
    if not norm:
        return ""
    return hashlib.md5(norm.encode("utf-8")).hexdigest()

def _calculate_score(rec: ReceiptRecord) -> int:
    """代表レコード選定用のスコア算出"""
    score = 0
    # 決定的な情報
    if rec.invoice_no_norm: score += 30
    if rec.invoice_candidate: score += 10
    
    # 必須情報の有無
    if rec.date: score += 20
    if rec.vendor and rec.vendor != "?": score += 15
    if rec.total_amount > 0: score += 15
    if rec.tax_rate_detected != TaxRate.UNKNOWN: score += 10
    if rec.payment_method != PaymentMethod.UNKNOWN: score += 10
    
    # 減点
    score -= len(rec.missing_fields) * 5
    if rec.needs_review: score -= 5
    
    return score

def _merge_records(records: List[ReceiptRecord]) -> AnalysisResult:
    """
    重複排除ロジック (Advanced)
    - 複数のキーでグループ化を試みる
    - スコアリングによる代表選定
    """
    logs = []
    n_before = len(records)
    logs.append(f"マージ前: {n_before}件")
    
    if not records:
        return AnalysisResult([], logs)

    # 1. 重複候補のグルーピング
    # どのレコードがどのレコードと同一かを判定するための Union-Find 的なアプローチ、
    # または単純に「強いキー」でバケット分けしていく。
    
    # ここでは優先順位付きのキーでグループIDを振る
    # グループID -> List[Record]
    groups = defaultdict(list)
    
    # 未割り当てのレコードインデックス
    remaining_indices = set(range(len(records)))



    # Strategy tracking
    # key: gid, value: reason string
    group_reasons = {}

    # 戦略4 (Fuzzy): (日付, 金額) が一致し、店名が部分一致 or 片方不明
    candidates = sorted(list(remaining_indices))
    skipped_in_fuzzy = set()
    
    for idx_i in range(len(candidates)):
        i = candidates[idx_i]
        if i in skipped_in_fuzzy:
            continue
            
        rec1 = records[i]
        # 日付と金額は必須 (これらが違うなら別人とする)
        if not (rec1.date and rec1.total_amount > 0):
            continue
            
        group_members = [i]
        v1 = _normalize_text(rec1.vendor)
        
        for idx_j in range(idx_i + 1, len(candidates)):
            j = candidates[idx_j]
            if j in skipped_in_fuzzy:
                continue
                
            rec2 = records[j]
            if rec1.date == rec2.date and rec1.total_amount == rec2.total_amount:
                v2 = _normalize_text(rec2.vendor)
                
                # 店名マッチ判定
                match = False
                if v1 == v2:
                    match = True
                elif not v1 or not v2 or v1 == "unknown" or v2 == "unknown":
                    # 片方が不明なら同一とみなす(日付金額一致の強さを信頼)
                    match = True
                elif v1 in v2 or v2 in v1:
                     # 部分一致 (例: "seven" in "seveneleven")
                    match = True
                
                if match:
                    group_members.append(j)
                    skipped_in_fuzzy.add(j)
        
        if len(group_members) > 1:
            # Fuzzyグループ成立
            gid = f"fuzzy_{i}"
            group_reasons[gid] = "Fuzzy Match (Date/Amount + Vendor)"
            for member_idx in group_members:
                groups[gid].append(records[member_idx])
                remaining_indices.discard(member_idx)
            skipped_in_fuzzy.add(i) # 自分自身もskip

    # 戦略2: (日付, 金額, 品目) - 店名がゆらいでいる場合 (Fuzzyで救えなかった場合)
    key_map_2 = defaultdict(list)
    for i in list(remaining_indices):
        rec = records[i]
        if rec.date and rec.total_amount > 0:
            subj_norm = _normalize_text(rec.subject)
            if subj_norm:
                key = (rec.date, rec.total_amount, subj_norm)
                key_map_2[key].append(i)

    for key, indices in key_map_2.items():
        if len(indices) > 1:
            gid = f"strat2_{indices[0]}"
            group_reasons[gid] = "Exact Match (Date/Amount/Subject)"
            for idx in indices:
                groups[gid].append(records[idx])
                remaining_indices.discard(idx)

    # 戦略3: (金額, 店名, 品目) - 日付読み取り失敗救済
    key_map_3 = defaultdict(list)
    for i in list(remaining_indices):
        rec = records[i]
        if rec.total_amount > 0 and rec.vendor and rec.vendor != "?":
             subj_norm = _normalize_text(rec.subject)
             key = (rec.total_amount, _normalize_text(rec.vendor), subj_norm)
             key_map_3[key].append(i)
             
    for key, indices in key_map_3.items():
        if len(indices) > 1:
            gid = f"strat3_{indices[0]}"
            group_reasons[gid] = "Fallback Match (Amount/Vendor/Subject)"
            for idx in indices:
                groups[gid].append(records[idx])
                remaining_indices.discard(idx)

    # 残りは孤立レコードとして扱う
    for i in remaining_indices:
        groups[i].append(records[i])

    # 2. 各グループ内でマージ実行
    merged_results = []
    
    for gid, group_recs in groups.items():
        if len(group_recs) == 1:
            merged_results.append(group_recs[0])
            continue
            
        # スコアリングで代表決定
        best_rec = max(group_recs, key=_calculate_score)
        
        # ログ記録
        others_count = len(group_recs) - 1
        logs.append(f"グループ統合({others_count+1}件): 代表='{best_rec.vendor}' ({best_rec.date}, ¥{best_rec.total_amount}) Score={_calculate_score(best_rec)}")
        
        # マージ処理: needs_review の OR条件、不足情報の補完
        # T番号は確定情報があればそれを優先
        final_invoice = best_rec.invoice_no_norm
        if not final_invoice:
            for r in group_recs:
                if r.invoice_no_norm:
                    final_invoice = r.invoice_no_norm
                    break
        
        # needs_review は「一つでもTrueならTrue」にするか？
        # いや、代表がCleanならCleanにしたい。ただし「T番号候補」などの情報は引き継ぎたい。
        # ここでは「代表のneeds_review」をベースにしつつ、
        # もし代表が T番号なし で 他のが T番号あり なら、T番号移植して needs_review を解消できるかも。
        
        if final_invoice and not best_rec.invoice_no_norm:
             best_rec.invoice_no_norm = final_invoice
             best_rec.qualified_flag = "○"
             
        # マージ候補の詳細を保存 (UI表示用)
        best_rec.group_id = str(gid)
        best_rec.merge_reason = group_reasons.get(str(gid), "Unknown Strategy")
        
        candidates_info = []
        for r in group_recs:
             candidates_info.append({
                 "date": r.date,
                 "vendor": r.vendor,
                 "total_amount": r.total_amount,
                 "invoice_no": r.invoice_no_norm,
                 "needs_review": r.needs_review,
                 "source": "split" if r.segment_id else "full" # 簡易判定
             })
        best_rec.merge_candidates = candidates_info
        
        merged_results.append(best_rec)

    n_after = len(merged_results)
    logs.append(f"マージ完了: {n_before}件 → {n_after}件")
    print("\n".join(logs))
    
    return AnalysisResult(merged_results, logs, raw_records=records)


def rescan_specific_area(image_path: str, region: List[int]) -> Optional[ReceiptRecord]:
    """
    指定された領域（0-1000正規化座標）を切り抜いて再解析する
    """
    if not region or len(region) != 4:
        return None
        
    ymin, xmin, ymax, xmax = region
    img = Image.open(image_path)
    w, h = img.size
    
    # 座標変換
    y1 = int((ymin / 1000) * h)
    x1 = int((xmin / 1000) * w)
    y2 = int((ymax / 1000) * h)
    x2 = int((xmax / 1000) * w)
    
    # マージンを追加 (少し広めに)
    margin_w = int(w * 0.05)
    margin_h = int(h * 0.05)
    y1 = max(0, y1 - margin_h)
    x1 = max(0, x1 - margin_w)
    y2 = min(h, y2 + margin_h)
    x2 = min(w, x2 + margin_w)
    
    crop_img = img.crop((x1, y1, x2, y2))
    
    base_dir = os.path.dirname(image_path)
    filename = os.path.splitext(os.path.basename(image_path))[0]
    temp_path = os.path.join(base_dir, f"{filename}_rescan_{datetime.now().strftime('%H%M%S')}.jpg")
    crop_img.save(temp_path, "JPEG")
    
    try:
        # 再解析 (単一画像として)
        records = _analyze_single_image(temp_path)
        if records:
            # 最も確からしい1件を返す (通常1件のはず)
            # 座標はローカル座標で返ってくるが、今回は更新対象レコードが決まっているので
            # 座標更新は必須ではない。
            return records[0]
        return None
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
