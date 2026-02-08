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
from dotenv import load_dotenv

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

この画像に含まれる**すべてのレシート/領収書**を検出し、それぞれについて
以下の情報をJSON配列で返してください。

各レシートに対して以下のフィールドを抽出：
{
  "date": "YYYY/MM/DD形式の日付",
  "vendor": "支払先/店名",
  "subject": "件名・品目の要約（例: タクシー代、昼食代、会費など）",
  "total_amount": 税込総額（整数）,
  "invoice_no_raw": "T番号（インボイス登録番号）があればそのまま記載。なければ空文字",
  "tax_rate": "10" or "8" or "8_reduced" or "unknown",
  "payment_clues": "支払方法の手がかり（お預り/お釣り→cash, PayPay→paypay, カード→credit, 不明→unknown）",
  "ocr_full_text": "レシート全文テキスト（改行は\\nで区切る）"
}

抽出ルール:
1. invoice_no_raw: 「登録番号」「適格請求書発行事業者」の近くにある T+数字列をそのまま記載
2. total_amount: 「計/合計/領収金額/乗車料金/お支払い」の近くの金額を優先
3. tax_rate: 「10%/8%/8.0%/10.0%」表記を探す。軽減税率(※)マークがあれば "8_reduced"
4. payment_clues: 「お預り/お釣り/釣銭」→cash、「PayPay」→paypay、「VISA/Master/JCB/カード」→credit
5. ocr_full_text: レシート全文を省略せずすべて記載すること（T番号抽出の後処理に使用）

**必ずJSON配列のみを返してください。マークダウンのコードブロックは不要です。**
レシートが1枚でも配列 [...] で返してください。
"""


def _image_to_base64(image_path: str) -> str:
    """画像をbase64文字列に変換"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


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

def analyze_receipt_image(image_path: str) -> List[ReceiptRecord]:
    """
    画像ファイルパスを受け取り、OCR → ReceiptRecord リストを返す。
    Gemini → 失敗時 OpenAI にフォールバック。

    後処理:
      - T番号: ラベル近傍優先 → 全文検索フォールバック (低信頼度)
      - 日付:  候補スコアリング (現在年±2優先)
    """
    raw_text = None
    backend_used = None

    # (1) Gemini を試す
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            print("[INFO] Gemini 2.0 Flash で解析を試みます...")
            raw_text = _call_gemini(image_path)
            backend_used = "Gemini"
        except Exception as e:
            print(f"[WARN] Gemini 失敗 ({type(e).__name__}): {e}")
            raw_text = None

    # (2) OpenAI にフォールバック
    if raw_text is None:
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                print("[INFO] OpenAI GPT-4o にフォールバックします...")
                raw_text = _call_openai(image_path)
                backend_used = "OpenAI"
            except Exception as e:
                print(f"[ERROR] OpenAI も失敗: {e}")
                return []
        else:
            print("[ERROR] 利用可能なAPIキーがありません")
            return []

    print(f"[INFO] バックエンド: {backend_used}")

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
        print(f"  [T番号] {vendor}: {invoice_debug}")

        # ── 日付 候補スコアリング ──
        ai_date = item.get("date", "")
        best_date, date_debug, date_needs_review = _extract_best_date(ocr_text, ai_date)
        if best_date != ai_date:
            print(f"  [日付] {vendor}: {date_debug}")

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

        # T番号が低信頼度で検出された場合 → candidate に格納、確定にはしない
        if invoice_low_conf:
            # 候補として保持するが確定にはしない
            invoice_confirmed = ""
            invoice_candidate = invoice_norm  # 補完済み候補
            needs_review = True
            if "invoice_no_candidate" not in missing:
                missing.append("invoice_no_candidate")
            print(f"  [T番号] {vendor}: 候補 {invoice_candidate} (低信頼度→未確定)")
        else:
            # 高信頼度 → 確定
            invoice_confirmed = invoice_norm
            invoice_candidate = ""

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
        )
        records.append(record)

    return records
