// Shapes of the backend packet the UI reads. The backend owns the full structure;
// only the fields rendered here are typed.

export type Price = {
  amount?: number | null;
  low?: number | null;
  high?: number | null;
  currency?: string;
  source?: string;
  url?: string;
  retrieved_at?: string;
  converted?: boolean;
  condition_assumed?: string;
};

export type Book = {
  id: string;
  shelf: string;
  frame_ref: string;
  status: string;
  title: string;
  author: string;
  proposed_title?: string;
  id_confidence?: number;
  spine_height_cm?: number | null;
  spine_thickness_cm?: number | null;
  replacement_cost?: Price;
  used_value?: Price;
  object_bbox?: number[] | null;
};

export type Item = {
  id: string;
  category: string;
  description: string;
  frame_ref: string;
  status: string;
  confidence?: number;
  is_print?: boolean;
  replacement_cost?: Price;
  dimensions_cm?: { w: number | null; h: number | null; d: number | null };
};

export type Packet = {
  sweep: { id: string; country: string; currency: string; country_code?: string; finished_at?: string; summary?: string };
  books: Book[];
  items: Item[];
  totals: Record<string, number>;
  review_queue: { ref_id: string; reason: string }[];
  frames?: { frame_ref: string; shelf?: string; quality?: { width: number; height: number } }[];
  guidance?: { text: string; action: string; code: string };
  transcript?: { id: string; role: string; text: string }[];
  room?: Record<string, number | string | null>;
  locale_comparison?: {
    base: { currency: string; country: string };
    comparison: { currency: string; country: string };
    rows: Record<string, unknown>[];
  } | null;
  performance?: { stages: Record<string, { mean_s: number; calls: number }> };
  verification_progress?: { status: string; completed: number; total: number };
  live_progress?: { stage: string; shelf?: string; frame_ref?: string; guidance?: string };
};

export type Locale = { country_code: string; country: string; currency: string };
