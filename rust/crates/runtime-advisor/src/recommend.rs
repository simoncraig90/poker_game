//! Top-level request/response types for the recommendation API.
//!
//! The entry point is `Router::recommend()` in `mode.rs`.

use crate::action::{AbstractSizeId, ActionKind, Street};
use crate::classify::{ClassificationQuality, SpotKey};
use crate::emergency::EmergencyDecision;
use crate::legalizer::{LegalizedAction, LiveLegalAction, SnapReason};
use crate::mode::Mode;
use engine_core::Card;

// ─── Request ─────────────────────────────────────────────────────────────────

/// Everything needed to produce a single action recommendation.
#[derive(Debug, Clone)]
pub struct RecommendRequest {
    // ── Classification ───────────────────────────────────────────────────────
    pub active_seats:       Vec<u8>,
    pub button_seat:        u8,
    pub hero_seat:          u8,
    pub street:             Street,
    pub effective_stack_bb:  f64,
    pub n_players_in_hand:  u8,
    pub action_history:     Vec<String>,
    pub board_bucket:       Option<u8>,
    pub rake_profile_str:   String,
    pub menu_version:       u8,

    // ── Hand ─────────────────────────────────────────────────────────────────
    pub hole_cards: [Card; 2],
    pub board:      Vec<Card>,

    // ── Game state ───────────────────────────────────────────────────────────
    pub facing_bet:       bool,
    pub pot:              f64,
    pub big_blind:        f64,
    pub hero_committed:   f64,
    pub hero_start_stack: f64,
    pub hero_stack:       f64,
    pub legal_actions:    Vec<LiveLegalAction>,

    // ── Size resolution context ──────────────────────────────────────────────
    pub facing_open_amount: f64,
    pub facing_3bet_amount: f64,
    pub facing_bet_amount:  f64,
}

// ─── Response ────────────────────────────────────────────────────────────────

/// Full recommendation result.
#[derive(Debug, Clone)]
pub struct RecommendResponse {
    pub mode:         Mode,
    pub action:       LegalizedAction,
    pub was_snapped:  bool,
    pub snap_reason:  SnapReason,
    pub spot_key:     SpotKey,
    pub quality:      ClassificationQuality,
    pub focus_hint:   FocusHint,
    /// Present only when mode == Emergency.
    pub emergency:    Option<EmergencyDecision>,
    /// Present only when mode == Exact.
    pub exact:        Option<ExactDecision>,
    /// Confidence in the recommendation (0.0–1.0).
    pub trust_score:  f64,
}

// ─── Focus hint ──────────────────────────────────────────────────────────────

/// Tells the overlay what visual treatment to use.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FocusHint {
    /// Solver-backed output — high confidence.
    Exact,
    /// Emergency fallback — show caution indicator.
    Emergency,
}

// ─── Exact decision diagnostics ──────────────────────────────────────────────

/// Diagnostic payload when the EXACT path fires.
#[derive(Debug, Clone)]
pub struct ExactDecision {
    pub hand_bucket:     engine_core::HandBucket,
    pub chosen_kind:     ActionKind,
    pub chosen_size:     AbstractSizeId,
    pub probability:     f32,
    pub resolved_amount: f64,
}
