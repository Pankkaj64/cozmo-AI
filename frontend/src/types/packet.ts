type CropVerification = { status: string; agreed: boolean; category?: string; reason?: string };

export type Book = {
  id: string;
  title: string;
  author: string;
  publisher?: string;
  edition?: string;
  proposed_publisher?: string;
  status: string;
  shelf: string;
  frame_ref: string;
  crop_verification?: CropVerification;
  isbn?: string;
  proposed_title?: string;
  proposed_author?: string;
  object_bbox?: number[];
  ocr_lines?: { text: string }[];
  partial?: boolean;
  description?: string;
  spine_height_cm?: number;
  spine_thickness_cm?: number;
  replacement_cost?: { amount?: number };
  used_value?: { amount?: number };
};

export type Packet = {
  verification_progress?: { status: string; completed: number; total: number };
  frames?: {
    frame_ref: string;
    shelf?: string;
    vision_status: string;
    count_status?: string;
    notes: string[];
    ocr_text?: string[];
    primary_count?: number;
    candidate_count?: number;
    validation?: { count: number | null };
  }[];
  guidance?: { code: string; text: string; action: string };
  transcript?: { id: string; role: string; text: string }[];
  workflow?: Record<string, { name: string; status: string; elapsed_s?: number }>;
  research?: {
    ref_id: string;
    identification?: { status: string; url?: string };
    pricing?: {
      status: string;
      reason?: string;
      offers: { url: string; listing_title: string; amount: number; currency: string; condition_assumed: string }[];
    };
    error?: string;
  }[];
  room?: Record<string, unknown>;
  audit_trail?: { id: string; time: string; step: string }[];
  videos?: { ref: string }[];
  sweep: { country: string; currency: string; duration_s: number };
  books: Book[];
  items: {
    id: string;
    category: string;
    object_bbox?: number[];
    material?: string;
    proposed_material?: string;
    proposed_brand_model?: string;
    reader_category?: string;
    dismissed_by_reader?: boolean;
    brand_model?: string;
    category_verified?: boolean;
    crop_verification?: CropVerification;
    is_print?: boolean;
    dimensions_cm?: { w: number | null; h: number | null; d: number | null };
    replacement_cost?: { low: number | null; high: number | null };
    description: string;
    status: string;
    frame_ref: string;
  }[];
  totals: Record<string, number>;
  review_queue: { ref_id: string; reason: string }[];
};
