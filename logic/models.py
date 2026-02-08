from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class TaxRate(str, Enum):
    RATE_10 = "10%"
    RATE_8 = "8%"
    RATE_8_REDUCED = "8%_reduced"
    EXEMPT = "exempt"
    UNKNOWN = "unknown"

class PaymentMethod(str, Enum):
    CASH = "cash"
    PAYPAY = "paypay"
    CREDIT = "credit"
    UNKNOWN = "unknown"

class Category(str, Enum):
    TRAVEL = "travel"          # 旅費交通費
    PARKING = "parking"        # 駐車場
    TOLL = "toll"              # 高速・通行料
    MEETING = "meeting"        # 会議費
    ENTERTAINMENT = "entertainment" # 交際費
    SUPPLIES = "supplies"      # 消耗品費
    DUES = "dues"              # 諸会費
    OTHER = "other"            # その他
    UNKNOWN = "unknown"        # 未設定

class ReceiptRecord(BaseModel):
    # Basic Info
    date: str = Field(..., description="YYYY/MM/DD")
    vendor: str = Field(..., description="支払先")
    subject: str = Field("", description="件名 (任意)")
    total_amount: int = Field(..., description="税込総額")
    
    # Invoice & Tax
    invoice_no_norm: str = Field("", description="確定済みT+13桁 (ハイフンなし)")
    invoice_candidate: str = Field("", description="未確定のT番号候補 (桁数補完等、要確認)")
    qualified_flag: str = Field("", description="'○' = 確定invoice_noあり")
    tax_rate_detected: TaxRate = Field(TaxRate.UNKNOWN, description="推定税率")
    
    # Payment & Category
    payment_method: PaymentMethod = Field(PaymentMethod.UNKNOWN, description="支払方法")
    category: Category = Field(Category.UNKNOWN, description="経費区分")
    
    # Internal Control
    needs_review: bool = Field(True, description="要確認フラグ")
    missing_fields: List[str] = Field(default_factory=list, description="不足項目のリスト")
    segment_id: Optional[str] = Field(None, description="画像分割ID")
    region: Optional[List[int]] = Field(None, description="[ymin, xmin, ymax, xmax] 0-1000正規化座標")
    
    # Merge Info
    merge_candidates: List[dict] = Field(default_factory=list, description="マージされた元レコードの簡易情報")
    merge_reason: str = Field("", description="マージの根拠 (例: 'Fuzzy Match: Vendor')")
    group_id: str = Field("", description="デバッグ用グループID")
    
    
    # User Confirmation
    # User Confirmation
    is_confirmed: bool = Field(False, description="ユーザー確認完了フラグ")
    is_discarded: bool = Field(False, description="ゴミ箱/削除フラグ (Trueなら一覧から除外)")
    
    # System Info
    backend_used: str = Field("", description="使用されたAIモデル (gemini-2.0-flash / gpt-4o 等)")
    image_path: str = Field("", description="ソース画像ファイル名 (input/images内)")
