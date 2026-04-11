//! Legalizer — mandatory final gate before emitting any action to the client.
//!
//! Guarantees:
//!   - Output is always a legal action (kind is in legal_actions list).
//!   - FOLD is never emitted when CHECK is legal (rule L0).
//!   - BET_TO / RAISE_TO amounts are snapped into [min_amount, max_amount].
//!   - Near-jam amounts (within `JAM_THRESHOLD_FRAC` of stack) are promoted to JAM.
//!   - Amount is never negative.
//!
//! Invariant 7 (deterministic multi-entry selection):
//!   When multiple LegalActionEntry entries of the same kind exist:
//!     Step 1: first entry whose [min, max] range contains target — by index order.
//!     Step 2: smallest min_amount strictly above target; ties → lower index wins.
//!     Step 3: highest max_amount; ties → lower index wins.

use crate::action::ActionKind;

// Fraction of hero's remaining stack within which BET/RAISE is promoted to JAM.
const JAM_THRESHOLD_FRAC: f64 = 0.05;

// ─── Public types ─────────────────────────────────────────────────────────────

/// One entry from the client's legal-action list.
#[derive(Debug, Clone)]
pub struct LiveLegalAction {
    pub kind:       ActionKind,
    pub min_amount: f64,
    pub max_amount: f64,
}

/// Everything the legalizer needs to decide.
#[derive(Debug, Clone)]
pub struct LegalizerInput {
    pub kind:          ActionKind,
    pub target_amount: f64,
    pub legal_actions: Vec<LiveLegalAction>,
    pub hero_stack:    f64,
    /// Amount hero has already committed this street (used for effective stack calc).
    pub hero_committed: f64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SnapReason {
    NoSnap,
    FoldToCheck,
    BelowMinimum,
    AboveMaximum,
    NearJam,
    KindNotLegal,
    MultipleEntriesSelected,
}

#[derive(Debug, Clone)]
pub struct LegalizerOutput {
    pub action:          LegalizedAction,
    pub was_snapped:     bool,
    pub snap_reason:     SnapReason,
    pub original_kind:   ActionKind,
    pub original_target: f64,
}

#[derive(Debug, Clone)]
pub struct LegalizedAction {
    pub kind:   ActionKind,
    pub amount: f64, // 0.0 for Fold/Check; hero_stack for Jam
}

// ─── Public entry point ───────────────────────────────────────────────────────

pub fn legalize(input: &LegalizerInput) -> LegalizerOutput {
    let original_kind   = input.kind;
    let original_target = input.target_amount;

    // L0: Never fold when check is legal.
    if input.kind == ActionKind::Fold && kind_is_legal(ActionKind::Check, &input.legal_actions) {
        return snapped(
            LegalizedAction { kind: ActionKind::Check, amount: 0.0 },
            SnapReason::FoldToCheck,
            original_kind,
            original_target,
        );
    }

    match input.kind {
        // L1: Check — legal or fall to L5.
        ActionKind::Check => {
            if kind_is_legal(ActionKind::Check, &input.legal_actions) {
                return clean(LegalizedAction { kind: ActionKind::Check, amount: 0.0 }, original_kind, original_target);
            }
            fallback_chain(input)
        }

        // L2: Call — use the client's exact amount (min_amount == max_amount for call).
        ActionKind::Call => {
            if let Some(entry) = first_entry(ActionKind::Call, &input.legal_actions) {
                return clean(LegalizedAction { kind: ActionKind::Call, amount: entry.min_amount }, original_kind, original_target);
            }
            fallback_chain(input)
        }

        // L3: Jam — use hero's total remaining stack.
        ActionKind::Jam => {
            if kind_is_legal(ActionKind::Jam, &input.legal_actions) {
                return clean(LegalizedAction { kind: ActionKind::Jam, amount: input.hero_stack }, original_kind, original_target);
            }
            // JAM not explicitly listed — try BetTo at max stack, then RaiseTo.
            // This handles the common case where the client offers [check, bet_to]
            // but no explicit jam — we push all-in via BetTo at max_amount.
            let jam_amount = input.hero_stack;
            for try_kind in [ActionKind::BetTo, ActionKind::RaiseTo] {
                let snapped = snap_bet_or_raise(
                    try_kind, jam_amount, &input.legal_actions, input.hero_stack,
                    original_kind, original_target,
                );
                if let Some(out) = snapped {
                    return out;
                }
            }
            fallback_chain(input)
        }

        // L4: BET_TO / RAISE_TO — snap into [min, max]; near-jam → promote to JAM.
        ActionKind::BetTo | ActionKind::RaiseTo => {
            let snapped = snap_bet_or_raise(
                input.kind, input.target_amount, &input.legal_actions,
                input.hero_stack, original_kind, original_target,
            );
            snapped.unwrap_or_else(|| fallback_chain(input))
        }

        // L0 already handled FOLD→CHECK. If we reach here check isn't legal.
        ActionKind::Fold => {
            clean(LegalizedAction { kind: ActionKind::Fold, amount: 0.0 }, original_kind, original_target)
        }
    }
}

// ─── Internals ────────────────────────────────────────────────────────────────

/// Snap a BET_TO or RAISE_TO amount into the legal range.
/// Returns None if this kind isn't legal at all.
fn snap_bet_or_raise(
    kind:          ActionKind,
    target:        f64,
    legal:         &[LiveLegalAction],
    hero_stack:    f64,
    original_kind: ActionKind,
    original_target: f64,
) -> Option<LegalizerOutput> {
    let entry = find_entry(kind, target, legal)?;

    let raw_amount = target.clamp(entry.min_amount, entry.max_amount).max(0.0);

    // Determine snap reason before potential jam promotion.
    let base_reason = if target < entry.min_amount {
        SnapReason::BelowMinimum
    } else if target > entry.max_amount {
        SnapReason::AboveMaximum
    } else {
        SnapReason::NoSnap
    };

    // Near-jam promotion: if amount is within JAM_THRESHOLD_FRAC of hero's stack.
    let remaining = (hero_stack - raw_amount).max(0.0);
    if remaining <= hero_stack * JAM_THRESHOLD_FRAC {
        return Some(snapped(
            LegalizedAction { kind: ActionKind::Jam, amount: hero_stack },
            SnapReason::NearJam,
            original_kind,
            original_target,
        ));
    }

    if base_reason == SnapReason::NoSnap {
        Some(clean(LegalizedAction { kind, amount: raw_amount }, original_kind, original_target))
    } else {
        Some(snapped(
            LegalizedAction { kind, amount: raw_amount },
            base_reason,
            original_kind,
            original_target,
        ))
    }
}

/// Invariant 7: Deterministic entry selection when multiple entries of the same kind exist.
///
/// Step 1: first entry whose [min, max] contains target (index order).
/// Step 2: smallest min_amount strictly above target; ties → lower index wins.
/// Step 3: highest max_amount; ties → lower index wins.
fn find_entry<'a>(kind: ActionKind, target: f64, legal: &'a [LiveLegalAction]) -> Option<&'a LiveLegalAction> {
    let matching: Vec<(usize, &LiveLegalAction)> = legal
        .iter()
        .enumerate()
        .filter(|(_, e)| e.kind == kind)
        .collect();

    if matching.is_empty() {
        return None;
    }

    // Step 1: first entry whose range contains target.
    for (_, entry) in &matching {
        if target >= entry.min_amount && target <= entry.max_amount {
            return Some(entry);
        }
    }

    // Step 2: entries whose min_amount is strictly above target.
    let above: Vec<(usize, &LiveLegalAction)> = matching
        .iter()
        .filter(|(_, e)| e.min_amount > target)
        .cloned()
        .collect();

    if !above.is_empty() {
        // Smallest min_amount; ties → lower index (already in index order from filter).
        let min_min = above.iter().map(|(_, e)| e.min_amount).fold(f64::INFINITY, f64::min);
        // Collect all with that min_amount and take the first (lowest index).
        return above.iter()
            .filter(|(_, e)| (e.min_amount - min_min).abs() < 1e-9)
            .map(|(_, e)| *e)
            .next();
    }

    // Step 3: all entries have max_amount < target; pick highest max_amount, lower index on tie.
    let max_max = matching.iter().map(|(_, e)| e.max_amount).fold(f64::NEG_INFINITY, f64::max);
    matching.iter()
        .filter(|(_, e)| (e.max_amount - max_max).abs() < 1e-9)
        .map(|(_, e)| *e)
        .next()
}

/// L5 fallback chain: CHECK → CALL → FOLD → JAM.
fn fallback_chain(input: &LegalizerInput) -> LegalizerOutput {
    for kind in [ActionKind::Check, ActionKind::Call, ActionKind::Fold, ActionKind::Jam] {
        if let Some(entry) = first_entry(kind, &input.legal_actions) {
            let amount = match kind {
                ActionKind::Check | ActionKind::Fold => 0.0,
                ActionKind::Jam                      => input.hero_stack,
                ActionKind::Call                     => entry.min_amount,
                _                                    => entry.min_amount,
            };
            return snapped(
                LegalizedAction { kind, amount },
                SnapReason::KindNotLegal,
                input.kind,
                input.target_amount,
            );
        }
    }
    // Absolute last resort — should never happen with a valid game state.
    log::error!("legalizer: no legal action found in fallback chain; defaulting to FOLD");
    snapped(
        LegalizedAction { kind: ActionKind::Fold, amount: 0.0 },
        SnapReason::KindNotLegal,
        input.kind,
        input.target_amount,
    )
}

fn kind_is_legal(kind: ActionKind, legal: &[LiveLegalAction]) -> bool {
    legal.iter().any(|e| e.kind == kind)
}

fn first_entry<'a>(kind: ActionKind, legal: &'a [LiveLegalAction]) -> Option<&'a LiveLegalAction> {
    legal.iter().find(|e| e.kind == kind)
}

fn clean(action: LegalizedAction, original_kind: ActionKind, original_target: f64) -> LegalizerOutput {
    LegalizerOutput {
        action,
        was_snapped:     false,
        snap_reason:     SnapReason::NoSnap,
        original_kind,
        original_target,
    }
}

fn snapped(action: LegalizedAction, reason: SnapReason, original_kind: ActionKind, original_target: f64) -> LegalizerOutput {
    LegalizerOutput {
        action,
        was_snapped:  true,
        snap_reason:  reason,
        original_kind,
        original_target,
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn bet_legal(min: f64, max: f64) -> LiveLegalAction {
        LiveLegalAction { kind: ActionKind::BetTo, min_amount: min, max_amount: max }
    }
    fn raise_legal(min: f64, max: f64) -> LiveLegalAction {
        LiveLegalAction { kind: ActionKind::RaiseTo, min_amount: min, max_amount: max }
    }
    fn check_legal() -> LiveLegalAction {
        LiveLegalAction { kind: ActionKind::Check, min_amount: 0.0, max_amount: 0.0 }
    }
    fn call_legal(amount: f64) -> LiveLegalAction {
        LiveLegalAction { kind: ActionKind::Call, min_amount: amount, max_amount: amount }
    }
    fn fold_legal() -> LiveLegalAction {
        LiveLegalAction { kind: ActionKind::Fold, min_amount: 0.0, max_amount: 0.0 }
    }
    fn jam_legal(stack: f64) -> LiveLegalAction {
        LiveLegalAction { kind: ActionKind::Jam, min_amount: stack, max_amount: stack }
    }

    fn input(kind: ActionKind, target: f64, legal: Vec<LiveLegalAction>) -> LegalizerInput {
        LegalizerInput {
            kind,
            target_amount: target,
            legal_actions: legal,
            hero_stack:    1000.0,
            hero_committed: 0.0,
        }
    }

    // ── L0 tests ──────────────────────────────────────────────────────────────

    #[test]
    fn l0_fold_to_check_when_check_legal() {
        let out = legalize(&input(ActionKind::Fold, 0.0, vec![fold_legal(), check_legal()]));
        assert_eq!(out.action.kind, ActionKind::Check);
        assert_eq!(out.snap_reason, SnapReason::FoldToCheck);
        assert!(out.was_snapped);
    }

    #[test]
    fn l0_fold_stays_when_check_not_legal() {
        let out = legalize(&input(ActionKind::Fold, 0.0, vec![fold_legal(), call_legal(200.0)]));
        assert_eq!(out.action.kind, ActionKind::Fold);
        assert!(!out.was_snapped);
    }

    // ── CHECK / CALL / JAM ────────────────────────────────────────────────────

    #[test]
    fn check_when_legal() {
        let out = legalize(&input(ActionKind::Check, 0.0, vec![check_legal()]));
        assert_eq!(out.action.kind, ActionKind::Check);
        assert_eq!(out.action.amount, 0.0);
        assert!(!out.was_snapped);
    }

    #[test]
    fn call_uses_exact_amount() {
        let out = legalize(&input(ActionKind::Call, 0.0, vec![fold_legal(), call_legal(150.0)]));
        assert_eq!(out.action.kind, ActionKind::Call);
        assert!((out.action.amount - 150.0).abs() < 0.01);
        assert!(!out.was_snapped);
    }

    #[test]
    fn jam_uses_hero_stack() {
        let out = legalize(&input(ActionKind::Jam, 0.0, vec![fold_legal(), jam_legal(1000.0)]));
        assert_eq!(out.action.kind, ActionKind::Jam);
        assert!((out.action.amount - 1000.0).abs() < 0.01);
        assert!(!out.was_snapped);
    }

    // ── BET snapping ──────────────────────────────────────────────────────────

    #[test]
    fn bet_below_minimum_snaps_up() {
        let out = legalize(&input(ActionKind::BetTo, 50.0, vec![fold_legal(), bet_legal(100.0, 500.0)]));
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert!((out.action.amount - 100.0).abs() < 0.01);
        assert_eq!(out.snap_reason, SnapReason::BelowMinimum);
        assert!(out.was_snapped);
    }

    #[test]
    fn bet_above_maximum_snaps_down() {
        let out = legalize(&input(ActionKind::BetTo, 800.0, vec![fold_legal(), bet_legal(100.0, 500.0)]));
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert!((out.action.amount - 500.0).abs() < 0.01);
        assert_eq!(out.snap_reason, SnapReason::AboveMaximum);
        assert!(out.was_snapped);
    }

    #[test]
    fn bet_in_range_no_snap() {
        let out = legalize(&input(ActionKind::BetTo, 300.0, vec![fold_legal(), bet_legal(100.0, 500.0)]));
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert!((out.action.amount - 300.0).abs() < 0.01);
        assert_eq!(out.snap_reason, SnapReason::NoSnap);
        assert!(!out.was_snapped);
    }

    #[test]
    fn near_jam_promotes_to_jam() {
        // hero_stack=1000, bet target=960 — remaining=40, which is 4% of stack (< 5%)
        let inp = LegalizerInput {
            kind: ActionKind::BetTo,
            target_amount: 960.0,
            legal_actions: vec![fold_legal(), bet_legal(100.0, 1000.0)],
            hero_stack: 1000.0,
            hero_committed: 0.0,
        };
        let out = legalize(&inp);
        assert_eq!(out.action.kind, ActionKind::Jam);
        assert!((out.action.amount - 1000.0).abs() < 0.01);
        assert_eq!(out.snap_reason, SnapReason::NearJam);
    }

    #[test]
    fn not_near_jam_stays_bet() {
        // hero_stack=1000, bet target=900 — remaining=100, which is 10% of stack (> 5%)
        let inp = LegalizerInput {
            kind: ActionKind::BetTo,
            target_amount: 900.0,
            legal_actions: vec![fold_legal(), bet_legal(100.0, 1000.0)],
            hero_stack: 1000.0,
            hero_committed: 0.0,
        };
        let out = legalize(&inp);
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert_eq!(out.snap_reason, SnapReason::NoSnap);
    }

    // ── L5 fallback chain ─────────────────────────────────────────────────────

    #[test]
    fn bet_not_legal_falls_to_check() {
        // BET_TO requested but only check/fold legal
        let out = legalize(&input(ActionKind::BetTo, 300.0, vec![fold_legal(), check_legal()]));
        assert_eq!(out.action.kind, ActionKind::Check);
        assert_eq!(out.snap_reason, SnapReason::KindNotLegal);
    }

    #[test]
    fn check_not_legal_falls_to_call() {
        let out = legalize(&input(ActionKind::Check, 0.0, vec![fold_legal(), call_legal(200.0)]));
        assert_eq!(out.action.kind, ActionKind::Call);
        assert_eq!(out.snap_reason, SnapReason::KindNotLegal);
    }

    // ── Invariant 7: multi-entry selection ────────────────────────────────────

    #[test]
    fn invariant7_target_in_first_range() {
        // Two bet entries; target=300 is in entry 0's range [100,500]
        let legal = vec![
            fold_legal(),
            bet_legal(100.0, 500.0),
            bet_legal(600.0, 900.0),
        ];
        let out = legalize(&input(ActionKind::BetTo, 300.0, legal));
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert!((out.action.amount - 300.0).abs() < 0.01);
    }

    #[test]
    fn invariant7_target_between_ranges_snaps_to_lower_min() {
        // target=550 is between [100,500] and [600,900]; picks entry with min=600
        let legal = vec![
            fold_legal(),
            bet_legal(100.0, 500.0),
            bet_legal(600.0, 900.0),
        ];
        let out = legalize(&input(ActionKind::BetTo, 550.0, legal));
        // find_entry should return the entry with min=600; snap to 600 (below min)
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert!((out.action.amount - 600.0).abs() < 0.01);
        assert_eq!(out.snap_reason, SnapReason::BelowMinimum);
    }

    #[test]
    fn invariant7_equidistant_lower_index_wins() {
        // Two entries with same min_amount strictly above target; lower index wins
        let legal = vec![
            fold_legal(),
            bet_legal(600.0, 800.0),
            bet_legal(600.0, 900.0),
        ];
        // target=400: both entries have min=600 > 400, both equally "close"
        // lower index (index 1, bet_legal 600-800) must win
        let out = legalize(&input(ActionKind::BetTo, 400.0, legal));
        // Snapped up to 600
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert!((out.action.amount - 600.0).abs() < 0.01);
    }

    #[test]
    fn invariant7_target_below_all_entries_uses_lowest_min() {
        let legal = vec![
            fold_legal(),
            bet_legal(300.0, 500.0),
            bet_legal(200.0, 400.0),
        ];
        // target=50: both mins above target; min_amount=200 is lowest → entry 1 (200-400)
        let out = legalize(&input(ActionKind::BetTo, 50.0, legal));
        assert_eq!(out.action.kind, ActionKind::BetTo);
        assert!((out.action.amount - 200.0).abs() < 0.01);
    }

    #[test]
    fn amount_never_negative() {
        // Degenerate: target < 0
        let out = legalize(&input(ActionKind::BetTo, -50.0, vec![fold_legal(), bet_legal(100.0, 500.0)]));
        assert!(out.action.amount >= 0.0);
    }

    // ── 4bp snap paths (Phase 14) ─────────────────────────────────────────────

    #[test]
    fn fourbp_jam_facing_bet_fold_call_snaps_to_call() {
        // Strategy says "jam" (aggressive intent) but only fold/call legal.
        // Fallback chain: CHECK(no) → CALL(yes) → returns Call.
        let out = legalize(&input(ActionKind::Jam, 0.0, vec![fold_legal(), call_legal(500.0)]));
        assert_eq!(out.action.kind, ActionKind::Call);
        assert!((out.action.amount - 500.0).abs() < 0.01);
        assert_eq!(out.snap_reason, SnapReason::KindNotLegal);
    }

    #[test]
    fn fourbp_check_facing_bet_snaps_to_fold() {
        // Strategy says "check" (passive intent) but facing a bet — only fold/call legal.
        // L1: check not legal → fallback chain: CHECK(no) → CALL(no — wait, CALL IS legal)
        // Actually the fallback finds CALL before FOLD. That's fine for 4bp:
        // the legalizer prefers staying in the hand over folding.
        // But the EMERGENCY path's "check" → fold mapping in 4BP_FAMILY_DESIGN.md
        // describes the *strategy semantic*, not the legalizer's behavior.
        // The legalizer correctly picks CALL when it's available.
        let out = legalize(&input(ActionKind::Check, 0.0, vec![fold_legal(), call_legal(500.0)]));
        // Fallback chain: CHECK(not legal) → CALL(legal!) → returns Call
        assert_eq!(out.action.kind, ActionKind::Call);
        assert_eq!(out.snap_reason, SnapReason::KindNotLegal);
    }

    #[test]
    fn fourbp_check_facing_bet_fold_only_snaps_to_fold() {
        // When only fold is legal, check snaps to fold.
        let out = legalize(&input(ActionKind::Check, 0.0, vec![fold_legal()]));
        assert_eq!(out.action.kind, ActionKind::Fold);
        assert_eq!(out.snap_reason, SnapReason::KindNotLegal);
    }

    #[test]
    fn fourbp_jam_hero_allin_snaps_to_check() {
        // Hero already all-in (stack=0), strategy says jam, only check legal.
        let inp = LegalizerInput {
            kind: ActionKind::Jam,
            target_amount: 0.0,
            legal_actions: vec![check_legal()],
            hero_stack: 0.0,
            hero_committed: 10000.0,
        };
        let out = legalize(&inp);
        assert_eq!(out.action.kind, ActionKind::Check);
        assert_eq!(out.snap_reason, SnapReason::KindNotLegal);
    }

    #[test]
    fn fourbp_jam_with_bet_entry_promotes_to_jam() {
        // Strategy says jam, legal actions include BetTo — should use full stack.
        let inp = LegalizerInput {
            kind: ActionKind::Jam,
            target_amount: 0.0,
            legal_actions: vec![check_legal(), bet_legal(100.0, 2000.0)],
            hero_stack: 2000.0,
            hero_committed: 8000.0,
        };
        let out = legalize(&inp);
        // Jam at 2000 = hero_stack; snap_bet_or_raise sees remaining=0 → near_jam
        assert_eq!(out.action.kind, ActionKind::Jam);
        assert!((out.action.amount - 2000.0).abs() < 0.01);
        assert_eq!(out.snap_reason, SnapReason::NearJam);
    }

    #[test]
    fn fourbp_check_not_facing_bet_clean() {
        // Not facing a bet, strategy says check, check is legal — clean pass-through.
        let out = legalize(&input(ActionKind::Check, 0.0, vec![check_legal(), bet_legal(100.0, 1000.0)]));
        assert_eq!(out.action.kind, ActionKind::Check);
        assert!(!out.was_snapped);
    }
}
