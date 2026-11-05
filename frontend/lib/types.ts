export interface Certificate {
  allowance_id: number;
  device_pubkey: string;
  per_tap_max: number;
  max_vouchers: number;
  allowed_categories: string[];
  blocked_categories: string[];
  expires_at: string;
}

export interface Voucher {
  allowance_id: number;
  serial: number;
  issued_at: string;
}

/** What the buyer's QR code carries. */
export interface QrPayload {
  certificate: Certificate;
  cert_signature: string;
  voucher: Voucher;
  device_signature: string;
}

/** What the merchant phone sends to POST /vouchers/settle. */
export interface OfflinePayment extends QrPayload {
  amount: number;
  merchant_id: number;
  scanned_at: string;
}

export interface Merchant {
  id: number;
  name: string;
  category: string;
  paypal_email: string;
}

export interface ReportItem {
  serial: number | null;
  amount: number;
  status: "SETTLED" | "SETTLED_PAYOUT_PENDING" | "REJECTED";
  reasons: string[];
  capture_id: string | null;
  payout_batch_id: string | null;
}

export interface SettlementReport {
  settled: number;
  rejected: number;
  payout_pending: number;
  total_settled_amount: number;
  items: ReportItem[];
  ai_review: { risk: "low" | "medium" | "high" | "unknown"; findings: string[] };
  summary: string;
}

export interface ParsedPolicy {
  policy: Record<string, unknown>;
  summary: string[];
  ambiguities: string[];
}

export interface AllowanceInfo {
  allowance_id: number;
  status: string;
  amount: number;
  captured_total: number;
  remaining: number;
  spent_today: number;
  expires_at: string | null;
}

export interface Approval {
  id: number;
  allowance_id: number;
  amount: number;
  category: string;
  merchant_name: string;
  channel: string;
  status: "PENDING" | "APPROVED" | "DENIED";
}

export interface ActivityItem {
  kind: "purchase" | "voucher" | "refund" | "approval";
  id: number;
  ts: string;
  title: string;
  detail: string;
  amount: number;
  status: string;
  reasons: string[];
  channel: string;
}
