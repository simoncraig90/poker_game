//! Emergency decision module — fallback when no solver artifact covers the spot.
//!
//! Uses the precomputed equity table (`EmergencyRangePrior`) to produce a
//! conservative action recommendation.  The output feeds directly into the
//! legalizer; the caller never needs to interpret it further.
//!
//! # Baseline policy
//! The EMERGENCY path is designed for **low regret**, not maximum EV:
//! - Default posture is **check / call / fold**.
//! - Small protection/value bets only for top-bucket hands, heads-up, in
//!   small or medium pots (SRP / limped).
//! - No normal postflop raises.
//! - Jams only when forced by short-stack or pot-committed geometry.
//! - No aggression multiway.  No aggression in large pots (3bp+).
//!
//! # Design principles
//! - Deterministic: same inputs always yield the same output.
//! - Cheap: one table lookup + a few comparisons; no allocation.

use crate::action::ActionKind;
use crate::classify::{PotClass, SpotKey, hero_is_oop};
use crate::emergency_range_prior::{AggressorRole, EmergencyRangePrior, PotClassIdx};
use engine_core::{BoardTexture, Card, HandBucket};

// ─── Equity thresholds ──────────────────────────────────────────────────────

/// Below this equity we fold when facing a bet.
const FOLD_THRESHOLD: f64 = 0.30;

/// Minimum equity for a small protection/value bet when checked to.
/// Only applies when aggression gates (HU, top bucket, small pot) pass.
const PROTECTION_BET_THRESHOLD: f64 = 0.68;

/// Protection bet size as fraction of pot.
const PROTECTION_BET_FRAC: f64 = 0.33;

// ─── Short-stack / pot-committed geometry ───────────────────────────────────

/// Effective stack (in bb) at or below which we allow a jam with top-bucket equity.
const SHORT_STACK_BB: f64 = 25.0;

/// If hero has already committed this fraction of effective stack, treat as
/// pot-committed — allow jam at a lower equity bar.
const POT_COMMITTED_FRAC: f64 = 0.67;

/// Equity required to jam when short-stacked or pot-committed.
const JAM_EQUITY_SHORT: f64 = 0.72;

/// Equity required to jam when NOT short/pot-committed (essentially never in
/// practice — requires top bucket in perfect context).
const JAM_EQUITY_DEEP: f64 = 0.92;

// ─── Input ──────────────────────────────────────────────────────────────────

/// Everything the emergency module needs beyond the SpotKey.
#[derive(Debug, Clone)]
pub struct EmergencyInput {
    /// Hero's two hole cards.
    pub hole_cards: [Card; 2],
    /// Community cards (0–5).
    pub board: Vec<Card>,
    /// Is hero facing a bet/raise this action? (false = checked to / first to act)
    pub facing_bet: bool,
    /// Current pot size in chips.
    pub pot: f64,
    /// Amount hero has already committed this hand (preflop + prior streets).
    pub hero_committed: f64,
    /// Hero's starting stack this hand.
    pub hero_start_stack: f64,
}

// ─── Output ─────────────────────────────────────────────────────────────────

/// Recommended action from the emergency path.
///
/// `target_amount` is advisory — the legalizer will snap it to legal bounds.
#[derive(Debug, Clone)]
pub struct EmergencyDecision {
    pub kind: ActionKind,
    /// Absolute chip amount (0.0 for Fold/Check/Call, pot-fraction for Bet).
    pub target_amount: f64,
    /// The equity value that drove the decision.
    pub equity: f64,
    /// The hand bucket used for the lookup.
    pub hand_bucket: HandBucket,
    /// The board texture used for the lookup.
    pub board_texture: BoardTexture,
}

// ─── Public entry point ─────────────────────────────────────────────────────

/// Produce an emergency action recommendation.
///
/// Caller guarantees:
/// - `prior` was loaded successfully at startup.
/// - `spot.street` is postflop (emergency preflop is not yet supported;
///   preflop coverage is high enough that Unknown should not occur).
pub fn decide_emergency(
    spot:  &SpotKey,
    input: &EmergencyInput,
    prior: &EmergencyRangePrior,
) -> EmergencyDecision {
    let hand_bucket   = engine_core::evaluate(input.hole_cards, &input.board);
    let board_texture = engine_core::classify_board_texture(&input.board);

    let pot_class_idx  = pot_class_to_idx(spot.pot_class);
    let aggressor_role = derive_aggressor_role(spot);

    let equity = prior.lookup(
        hand_bucket,
        board_texture,
        pot_class_idx,
        aggressor_role,
        spot.n_players,
    );

    let ctx = SpotContext {
        is_hu:           spot.n_players == 2,
        is_small_pot:    matches!(spot.pot_class, PotClass::Limped | PotClass::Srp),
        is_short_stack:  spot.effective_stack_bb <= SHORT_STACK_BB,
        is_pot_committed: input.hero_start_stack > 0.0
            && (input.hero_committed / input.hero_start_stack) >= POT_COMMITTED_FRAC,
        is_top_bucket:   matches!(hand_bucket, HandBucket::Monster | HandBucket::VeryStrong),
    };

    let (kind, target_amount) = if input.facing_bet {
        decide_facing_bet(equity, &ctx)
    } else {
        decide_checked_to(equity, input.pot, &ctx)
    };

    EmergencyDecision {
        kind,
        target_amount,
        equity,
        hand_bucket,
        board_texture,
    }
}

// ─── Internal context for decision gates ────────────────────────────────────

struct SpotContext {
    is_hu:            bool,
    is_small_pot:     bool,
    is_short_stack:   bool,
    is_pot_committed: bool,
    is_top_bucket:    bool,
}

impl SpotContext {
    /// Whether the geometry forces a jam (short stack or pot committed).
    fn jam_forced(&self) -> bool {
        self.is_short_stack || self.is_pot_committed
    }

    /// Whether we allow any voluntary aggression (bet or raise).
    /// HU + small pot + top bucket only.
    fn aggression_allowed(&self) -> bool {
        self.is_hu && self.is_small_pot && self.is_top_bucket
    }
}

// ─── Decision branches ──────────────────────────────────────────────────────

/// Hero is facing a bet or raise.
///
/// Policy: fold / call / jam-if-forced.  No normal raises.
fn decide_facing_bet(equity: f64, ctx: &SpotContext) -> (ActionKind, f64) {
    // Jam gate: only when geometry forces it AND equity is high enough.
    if ctx.jam_forced() && equity >= JAM_EQUITY_SHORT {
        return (ActionKind::Jam, 0.0);
    }
    // Deep jam: essentially never — requires extreme equity (top bucket
    // in perfect context is the only way to hit 0.92+ in the prior).
    if ctx.aggression_allowed() && equity >= JAM_EQUITY_DEEP {
        return (ActionKind::Jam, 0.0);
    }

    // Otherwise: call or fold.
    if equity >= FOLD_THRESHOLD {
        (ActionKind::Call, 0.0)
    } else {
        (ActionKind::Fold, 0.0)
    }
}

/// Hero is checked to or first to act.
///
/// Policy: default check.  Small protection bet only for top bucket, HU,
/// small pot, with sufficient equity.  No jam unless forced.
fn decide_checked_to(equity: f64, pot: f64, ctx: &SpotContext) -> (ActionKind, f64) {
    // Forced jam (short stack / pot committed + high equity).
    if ctx.jam_forced() && equity >= JAM_EQUITY_SHORT {
        return (ActionKind::Jam, 0.0);
    }

    // Small protection/value bet — gated tightly.
    if ctx.aggression_allowed() && equity >= PROTECTION_BET_THRESHOLD {
        let bet_amount = pot * PROTECTION_BET_FRAC;
        return (ActionKind::BetTo, bet_amount);
    }

    // Default: check.
    (ActionKind::Check, 0.0)
}

// ─── Dimension mapping ──────────────────────────────────────────────────────

fn pot_class_to_idx(pc: PotClass) -> PotClassIdx {
    match pc {
        PotClass::Limped   => PotClassIdx::Limped,
        PotClass::Srp      => PotClassIdx::Srp,
        PotClass::ThreeBp  => PotClassIdx::ThreeBp,
        PotClass::FourBp | PotClass::Squeeze => PotClassIdx::FourBpOrSqueeze,
    }
}

fn derive_aggressor_role(spot: &SpotKey) -> AggressorRole {
    match spot.aggressor_pos {
        None => AggressorRole::None,
        Some(agg_pos) => {
            if hero_is_oop(agg_pos, spot.hero_pos) {
                AggressorRole::Oop
            } else {
                AggressorRole::Ip
            }
        }
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::action::Street;
    use crate::classify::{Position, PotClass, RakeProfile, StackBucket};
    use crate::emergency_range_prior::*;
    use engine_core::parse_card;
    use tempfile::tempdir;

    fn build_prior(fill: f64) -> (tempfile::TempDir, std::path::PathBuf, std::path::PathBuf) {
        use artifact_store::checksum_bytes;

        let dir = tempdir().unwrap();
        let bin_path      = dir.path().join("prior.bin");
        let manifest_path = dir.path().join("prior.manifest.json");

        let data: Vec<u8> = (0..TABLE_LEN)
            .flat_map(|_| fill.to_le_bytes())
            .collect();
        std::fs::write(&bin_path, &data).unwrap();

        let checksum = checksum_bytes(&data);
        let manifest = serde_json::json!({
            "artifact_type":    "emergency_range_prior",
            "version":           1u32,
            "checksum_sha256":   checksum,
            "file_size_bytes":   data.len() as u64,
            "n_hand_buckets":    N_HAND_BUCKETS,
            "n_board_textures":  N_BOARD_TEXTURES,
            "n_pot_classes":     N_POT_CLASSES,
            "n_aggressor_roles": N_AGGRESSOR_ROLES,
            "n_player_buckets":  N_PLAYER_BUCKETS,
        });
        std::fs::write(&manifest_path, serde_json::to_string_pretty(&manifest).unwrap()).unwrap();

        (dir, bin_path, manifest_path)
    }

    fn make_spot_full(
        pot_class: PotClass,
        agg: Option<Position>,
        hero: Position,
        n: u8,
        stack_bucket: StackBucket,
        eff_bb: f64,
    ) -> SpotKey {
        SpotKey {
            pot_class,
            street: Street::Flop,
            aggressor_pos: agg,
            hero_pos: hero,
            n_players: n,
            stack_bucket,
            effective_stack_bb: eff_bb,
            board_bucket: Some(42),
            rake_profile: RakeProfile::NoRake,
            menu_version: 1,
        }
    }

    fn make_spot(pot_class: PotClass, agg: Option<Position>, hero: Position, n: u8) -> SpotKey {
        make_spot_full(pot_class, agg, hero, n, StackBucket::S100, 100.0)
    }

    fn make_input(facing_bet: bool) -> EmergencyInput {
        EmergencyInput {
            hole_cards: [parse_card("As").unwrap(), parse_card("Kd").unwrap()],
            board: vec![
                parse_card("Ah").unwrap(),
                parse_card("7c").unwrap(),
                parse_card("2s").unwrap(),
            ],
            facing_bet,
            pot: 600.0,
            hero_committed: 300.0,
            hero_start_stack: 10000.0,
        }
    }

    // ── Core conservative behaviour: facing bet ──────────────────────────────

    #[test]
    fn facing_bet_low_equity_folds() {
        let (_dir, bin, mf) = build_prior(0.15);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        let input = make_input(true);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Fold);
    }

    #[test]
    fn facing_bet_medium_equity_calls() {
        let (_dir, bin, mf) = build_prior(0.50);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        let input = make_input(true);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Call);
    }

    #[test]
    fn facing_bet_high_equity_still_calls_when_deep() {
        // 0.78 equity, 100bb deep, not pot committed — should call, NOT raise.
        let (_dir, bin, mf) = build_prior(0.78);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        let input = make_input(true);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Call, "deep stack should call, not raise");
    }

    #[test]
    fn facing_bet_no_raise_ever_at_normal_depth() {
        // Even very high equity (0.88) at normal depth should not produce a raise.
        let (_dir, bin, mf) = build_prior(0.88);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        let input = make_input(true);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_ne!(dec.kind, ActionKind::RaiseTo, "EMERGENCY should never raise at normal depth");
    }

    // ── Jam gates ────────────────────────────────────────────────────────────

    #[test]
    fn short_stack_high_equity_jams_facing_bet() {
        let (_dir, bin, mf) = build_prior(0.80);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot_full(
            PotClass::Srp, Some(Position::Btn), Position::Bb, 2,
            StackBucket::S40, 20.0,  // short stack
        );
        let input = make_input(true);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Jam);
    }

    #[test]
    fn short_stack_medium_equity_calls_not_jams() {
        let (_dir, bin, mf) = build_prior(0.55);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot_full(
            PotClass::Srp, Some(Position::Btn), Position::Bb, 2,
            StackBucket::S40, 20.0,
        );
        let input = make_input(true);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Call, "short but not enough equity to jam");
    }

    #[test]
    fn pot_committed_high_equity_jams() {
        let (_dir, bin, mf) = build_prior(0.80);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        // Committed 7000 of 10000 starting stack = 70% > 67% threshold.
        let mut input = make_input(true);
        input.hero_committed = 7000.0;
        input.hero_start_stack = 10000.0;

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Jam);
    }

    #[test]
    fn deep_not_committed_does_not_jam_at_moderate_equity() {
        let (_dir, bin, mf) = build_prior(0.80);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        let input = make_input(true); // hero_committed=300 of 10000 = 3%

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Call, "deep + not committed = no jam at 0.80");
    }

    // ── Checked to: conservative by default ──────────────────────────────────

    #[test]
    fn checked_to_medium_equity_checks() {
        let (_dir, bin, mf) = build_prior(0.55);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, None, Position::Bb, 2);
        let input = make_input(false);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Check);
    }

    #[test]
    fn checked_to_high_equity_checks_in_three_bet_pot() {
        // Even top equity should not bet in a 3bp — not a small pot.
        let (_dir, bin, mf) = build_prior(0.85);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::ThreeBp, Some(Position::Co), Position::Bb, 2);
        let input = make_input(false);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Check, "no bet in large pot");
    }

    #[test]
    fn checked_to_high_equity_checks_multiway() {
        // No aggression multiway, even with great equity.
        let (_dir, bin, mf) = build_prior(0.85);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 3);
        let input = make_input(false);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Check, "no bet multiway");
    }

    #[test]
    fn checked_to_short_stack_jams_high_equity() {
        let (_dir, bin, mf) = build_prior(0.80);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot = make_spot_full(
            PotClass::Srp, None, Position::Bb, 2,
            StackBucket::S40, 18.0,
        );
        let input = make_input(false);

        let dec = decide_emergency(&spot, &input, &prior);
        assert_eq!(dec.kind, ActionKind::Jam);
    }

    // ── Protection bet: tightly gated ────────────────────────────────────────

    // Note: protection bets require is_top_bucket (Monster/VeryStrong).
    // With a uniform prior, we can't control hand_bucket — it depends on
    // engine_core::evaluate.  So we test the gate logic on the SpotContext
    // directly and rely on the decide_checked_to unit tests.

    #[test]
    fn protection_bet_gate_hu_small_pot() {
        let ctx = SpotContext {
            is_hu: true,
            is_small_pot: true,
            is_short_stack: false,
            is_pot_committed: false,
            is_top_bucket: true,
        };
        // Equity above threshold → should bet.
        let (kind, amount) = decide_checked_to(0.75, 600.0, &ctx);
        assert_eq!(kind, ActionKind::BetTo);
        assert!((amount - 198.0).abs() < 1.0, "33% of 600 = 198, got {amount}");
    }

    #[test]
    fn protection_bet_blocked_multiway() {
        let ctx = SpotContext {
            is_hu: false,
            is_small_pot: true,
            is_short_stack: false,
            is_pot_committed: false,
            is_top_bucket: true,
        };
        let (kind, _) = decide_checked_to(0.75, 600.0, &ctx);
        assert_eq!(kind, ActionKind::Check, "no bet multiway");
    }

    #[test]
    fn protection_bet_blocked_large_pot() {
        let ctx = SpotContext {
            is_hu: true,
            is_small_pot: false, // 3bp
            is_short_stack: false,
            is_pot_committed: false,
            is_top_bucket: true,
        };
        let (kind, _) = decide_checked_to(0.75, 600.0, &ctx);
        assert_eq!(kind, ActionKind::Check, "no bet in large pot");
    }

    #[test]
    fn protection_bet_blocked_non_top_bucket() {
        let ctx = SpotContext {
            is_hu: true,
            is_small_pot: true,
            is_short_stack: false,
            is_pot_committed: false,
            is_top_bucket: false,
        };
        let (kind, _) = decide_checked_to(0.75, 600.0, &ctx);
        assert_eq!(kind, ActionKind::Check, "no bet without top bucket");
    }

    #[test]
    fn protection_bet_below_equity_threshold_checks() {
        let ctx = SpotContext {
            is_hu: true,
            is_small_pot: true,
            is_short_stack: false,
            is_pot_committed: false,
            is_top_bucket: true,
        };
        let (kind, _) = decide_checked_to(0.60, 600.0, &ctx);
        assert_eq!(kind, ActionKind::Check, "below protection equity threshold");
    }

    // ── No RaiseTo ever produced ─────────────────────────────────────────────

    #[test]
    fn no_raise_at_any_equity() {
        // Sweep equity 0.0–1.0 and verify no RaiseTo is ever produced.
        for fill_pct in 0..=100 {
            let equity = fill_pct as f64 / 100.0;
            let ctx_hu = SpotContext {
                is_hu: true, is_small_pot: true, is_short_stack: false,
                is_pot_committed: false, is_top_bucket: true,
            };
            let ctx_mw = SpotContext {
                is_hu: false, is_small_pot: true, is_short_stack: false,
                is_pot_committed: false, is_top_bucket: true,
            };
            let (kind_fb_hu, _) = decide_facing_bet(equity, &ctx_hu);
            let (kind_fb_mw, _) = decide_facing_bet(equity, &ctx_mw);
            let (kind_ct_hu, _) = decide_checked_to(equity, 600.0, &ctx_hu);
            let (kind_ct_mw, _) = decide_checked_to(equity, 600.0, &ctx_mw);
            assert_ne!(kind_fb_hu, ActionKind::RaiseTo, "facing bet HU at {equity}");
            assert_ne!(kind_fb_mw, ActionKind::RaiseTo, "facing bet MW at {equity}");
            assert_ne!(kind_ct_hu, ActionKind::RaiseTo, "checked to HU at {equity}");
            assert_ne!(kind_ct_mw, ActionKind::RaiseTo, "checked to MW at {equity}");
        }
    }

    // ── Dimension mapping (unchanged) ────────────────────────────────────────

    #[test]
    fn pot_class_mapping() {
        assert_eq!(pot_class_to_idx(PotClass::Limped)  as u8, PotClassIdx::Limped as u8);
        assert_eq!(pot_class_to_idx(PotClass::Srp)     as u8, PotClassIdx::Srp as u8);
        assert_eq!(pot_class_to_idx(PotClass::ThreeBp) as u8, PotClassIdx::ThreeBp as u8);
        assert_eq!(pot_class_to_idx(PotClass::FourBp)  as u8, PotClassIdx::FourBpOrSqueeze as u8);
        assert_eq!(pot_class_to_idx(PotClass::Squeeze) as u8, PotClassIdx::FourBpOrSqueeze as u8);
    }

    #[test]
    fn aggressor_role_none_for_limped() {
        let spot = make_spot(PotClass::Limped, None, Position::Bb, 3);
        assert!(matches!(derive_aggressor_role(&spot), AggressorRole::None));
    }

    #[test]
    fn aggressor_role_ip_when_btn_aggresses_vs_bb() {
        let spot = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        assert!(matches!(derive_aggressor_role(&spot), AggressorRole::Ip));
    }

    #[test]
    fn aggressor_role_oop_when_sb_aggresses_vs_btn() {
        let spot = make_spot(PotClass::ThreeBp, Some(Position::Sb), Position::Btn, 2);
        assert!(matches!(derive_aggressor_role(&spot), AggressorRole::Oop));
    }

    // ── Output carries diagnostics ───────────────────────────────────────────

    #[test]
    fn decision_carries_hand_and_board_info() {
        let (_dir, bin, mf) = build_prior(0.50);
        let prior = EmergencyRangePrior::load(&bin, &mf).unwrap();
        let spot  = make_spot(PotClass::Srp, Some(Position::Btn), Position::Bb, 2);
        let input = make_input(true);

        let dec = decide_emergency(&spot, &input, &prior);
        assert!(dec.equity >= 0.0 && dec.equity <= 1.0);
    }
}
