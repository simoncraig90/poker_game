//! Spot classification: position arithmetic, pot class, stack bucket, SpotKey.

use crate::action::Street;
use thiserror::Error;

// ─── Enumerations ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PotClass {
    Limped,
    Srp,       // single raised pot
    ThreeBp,   // 3-bet pot
    FourBp,    // 4-bet pot
    Squeeze,   // squeeze (3-bet with a caller already in)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Position {
    Utg,
    Hj,
    Co,
    Btn,
    Sb,
    Bb,
}

impl Position {
    /// Human-readable lowercase string matching artifact_key conventions.
    pub fn as_str(self) -> &'static str {
        match self {
            Position::Utg => "utg",
            Position::Hj  => "hj",
            Position::Co  => "co",
            Position::Btn => "btn",
            Position::Sb  => "sb",
            Position::Bb  => "bb",
        }
    }
}

/// Effective stack depth in big blinds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum StackBucket {
    S40,       // ≤ 50bb
    S60,       // 51–80bb
    S100,      // 81–125bb  (canonical 100bb)
    S150,      // 126–175bb
    S200Plus,  // > 175bb
}

impl StackBucket {
    pub fn as_str(self) -> &'static str {
        match self {
            StackBucket::S40      => "s40",
            StackBucket::S60      => "s60",
            StackBucket::S100     => "s100",
            StackBucket::S150     => "s150",
            StackBucket::S200Plus => "s200",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RakeProfile {
    NoRake,
    Rake5Cap1Bb,
    Rake5Cap05Bb,
}

impl RakeProfile {
    pub fn as_str(self) -> &'static str {
        match self {
            RakeProfile::NoRake       => "norake",
            RakeProfile::Rake5Cap1Bb  => "rake5cap1bb",
            RakeProfile::Rake5Cap05Bb => "rake5cap05bb",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "norake"        => Some(RakeProfile::NoRake),
            "rake5cap1bb"   => Some(RakeProfile::Rake5Cap1Bb),
            "rake5cap05bb"  => Some(RakeProfile::Rake5Cap05Bb),
            _               => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClassificationQuality {
    /// Direct match — artifact lookup should succeed.
    Exact,
    /// Nearest supported abstraction — low-confidence flag warranted.
    Approximate,
    /// Unrecognised spot — EMERGENCY mode required.
    Unknown,
}

// ─── SpotKey ──────────────────────────────────────────────────────────────────

/// Fully-typed classification of a game spot.
/// The artifact_key string is derived deterministically from this struct.
#[derive(Debug, Clone)]
pub struct SpotKey {
    pub pot_class:          PotClass,
    pub street:             Street,
    /// Position of the last preflop aggressor (None for limped pots).
    pub aggressor_pos:      Option<Position>,
    pub hero_pos:           Position,
    pub n_players:          u8,
    pub stack_bucket:       StackBucket,
    pub effective_stack_bb: f64,
    /// Board cluster index (0–98); None preflop.
    pub board_bucket:       Option<u8>,
    pub rake_profile:       RakeProfile,
    pub menu_version:       u8,
}

// ─── Classification errors ────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum ClassifyError {
    #[error("seat count out of range: {0} (expected 2–6)")]
    SeatCountInvalid(u8),

    #[error("hero_seat {hero} not found among active seats")]
    HeroSeatNotFound { hero: u8 },

    #[error("button_seat {btn} not found among active seats")]
    ButtonSeatNotFound { btn: u8 },

    #[error("unknown rake profile: {0}")]
    UnknownRakeProfile(String),

    #[error("action_history entry could not be parsed: {0}")]
    ActionHistoryParseError(String),
}

// ─── Input to classify_spot ───────────────────────────────────────────────────

/// Everything needed to produce a SpotKey from raw game state.
pub struct ClassifyInput<'a> {
    /// All seat numbers at the table (for position arithmetic), e.g. [1,2,3,4,5,6].
    /// Used only to derive BTN/SB/BB/UTG/HJ/CO labels via offset arithmetic.
    pub active_seats: &'a [u8],
    /// Seat number designated as the button.
    pub button_seat: u8,
    /// Hero's seat number.
    pub hero_seat: u8,
    pub street: Street,
    pub effective_stack_bb: f64,
    /// Number of players still live in this pot (2 = heads-up, 3 = 3-way, etc.).
    /// Caller derives this from the game state; used for SpotKey.n_players and
    /// classification quality gating.
    pub n_players_in_hand: u8,
    /// Action history this hand in format "SEAT:ACTION[:AMOUNT]".
    /// Only BET_TO and RAISE_TO entries matter for pot class counting.
    pub action_history: &'a [&'a str],
    /// Board cluster index from board_clusters.py (None preflop).
    pub board_bucket: Option<u8>,
    pub rake_profile_str: &'a str,
    pub menu_version: u8,
}

// ─── classify_spot ────────────────────────────────────────────────────────────

pub fn classify_spot(inp: &ClassifyInput) -> Result<(SpotKey, ClassificationQuality), ClassifyError> {
    let n_seats = inp.active_seats.len() as u8;
    if n_seats < 2 || n_seats > 6 {
        return Err(ClassifyError::SeatCountInvalid(n_seats));
    }

    // ── Find button index within active_seats ─────────────────────────────────
    let btn_idx = inp.active_seats.iter().position(|&s| s == inp.button_seat)
        .ok_or(ClassifyError::ButtonSeatNotFound { btn: inp.button_seat })?;

    // ── Hero position ─────────────────────────────────────────────────────────
    let hero_idx = inp.active_seats.iter().position(|&s| s == inp.hero_seat)
        .ok_or(ClassifyError::HeroSeatNotFound { hero: inp.hero_seat })?;

    let hero_pos = seat_to_position(hero_idx, btn_idx, n_seats);

    // ── Pot class from action history ─────────────────────────────────────────
    let (pot_class, aggressor_seat) = classify_pot(inp.action_history, inp.active_seats)?;

    // ── Aggressor position ────────────────────────────────────────────────────
    let aggressor_pos = aggressor_seat.map(|seat| {
        let agg_idx = inp.active_seats.iter().position(|&s| s == seat).unwrap();
        seat_to_position(agg_idx, btn_idx, n_seats)
    });

    // ── Stack bucket ──────────────────────────────────────────────────────────
    let stack_bucket = stack_to_bucket(inp.effective_stack_bb);

    // ── Rake profile ──────────────────────────────────────────────────────────
    let rake_profile = RakeProfile::from_str(inp.rake_profile_str)
        .ok_or_else(|| ClassifyError::UnknownRakeProfile(inp.rake_profile_str.to_string()))?;

    let n_players = inp.n_players_in_hand;

    // ── Classification quality ────────────────────────────────────────────────
    // Phase 1 baseline: Exact only for SRP, 2-way, 100bb, no rake.
    // Everything else is Approximate (or Unknown for deep multiway).
    let quality = classification_quality(pot_class, n_players, stack_bucket, rake_profile);

    let key = SpotKey {
        pot_class,
        street: inp.street,
        aggressor_pos,
        hero_pos,
        n_players,
        stack_bucket,
        effective_stack_bb: inp.effective_stack_bb,
        board_bucket: inp.board_bucket,
        rake_profile,
        menu_version: inp.menu_version,
    };

    Ok((key, quality))
}

// ─── Position arithmetic ──────────────────────────────────────────────────────

/// Derive position from seat index relative to the button.
///
/// `seat_idx` and `btn_idx` are indices into the active_seats slice (0-based).
/// Offset = (seat_idx - btn_idx + n_seats) % n_seats
///
/// 6-max mapping: offset 0=BTN, 1=SB, 2=BB, 3=UTG, 4=HJ, 5=CO
/// Fewer players: collapse from UTG outward (drop HJ first, then UTG, etc.)
pub fn seat_to_position(seat_idx: usize, btn_idx: usize, n_seats: u8) -> Position {
    let offset = (seat_idx + n_seats as usize - btn_idx) % n_seats as usize;
    match (n_seats, offset) {
        // 6-handed
        (6, 0) => Position::Btn,
        (6, 1) => Position::Sb,
        (6, 2) => Position::Bb,
        (6, 3) => Position::Utg,
        (6, 4) => Position::Hj,
        (6, 5) => Position::Co,
        // 5-handed (no UTG)
        (5, 0) => Position::Btn,
        (5, 1) => Position::Sb,
        (5, 2) => Position::Bb,
        (5, 3) => Position::Hj,
        (5, 4) => Position::Co,
        // 4-handed (no UTG/HJ)
        (4, 0) => Position::Btn,
        (4, 1) => Position::Sb,
        (4, 2) => Position::Bb,
        (4, 3) => Position::Co,
        // 3-handed
        (3, 0) => Position::Btn,
        (3, 1) => Position::Sb,
        (3, 2) => Position::Bb,
        // Heads-up: BTN=SB acts first preflop, BB acts first postflop
        (2, 0) => Position::Btn,
        (2, 1) => Position::Bb,
        // Fallback (shouldn't happen given seat count validation)
        _ => Position::Bb,
    }
}

/// True if hero acts before villain postflop (OOP).
///
/// Postflop order by offset from btn (ascending): SB(1), BB(2), UTG(3), HJ(4), CO(5), BTN(0→6)
/// Hero is OOP if hero's postflop index < villain's.
pub fn hero_is_oop(hero_pos: Position, villain_pos: Position) -> bool {
    postflop_order(hero_pos) < postflop_order(villain_pos)
}

fn postflop_order(pos: Position) -> u8 {
    match pos {
        Position::Sb  => 1,
        Position::Bb  => 2,
        Position::Utg => 3,
        Position::Hj  => 4,
        Position::Co  => 5,
        Position::Btn => 6, // BTN acts last postflop
    }
}

// ─── Pot class classification ─────────────────────────────────────────────────

/// Count aggressive preflop actions and identify the original opener seat.
///
/// A BET_TO or RAISE_TO action in the preflop action_history increments the
/// aggression counter. The seat of the **first** such action is the aggressor
/// (the original opener), not the last. This ensures that in 3bp/4bp pots the
/// artifact key describes the opener-vs-hero matchup, giving correct IP/OOP
/// derivation. For SRP there is only one aggression so first == last.
///
/// Action format: "SEAT:ACTION" or "SEAT:ACTION:AMOUNT"
fn classify_pot(
    action_history: &[&str],
    _active_seats: &[u8],
) -> Result<(PotClass, Option<u8>), ClassifyError> {
    let mut n_aggressive = 0u32;
    let mut first_aggressor_seat: Option<u8> = None;
    let mut had_caller_before_squeeze = false;
    let mut squeeze_candidate = false;

    for entry in action_history {
        let parts: Vec<&str> = entry.splitn(3, ':').collect();
        if parts.len() < 2 {
            return Err(ClassifyError::ActionHistoryParseError(entry.to_string()));
        }
        let seat_str = parts[0];
        let action   = parts[1];

        let seat: u8 = seat_str.parse()
            .map_err(|_| ClassifyError::ActionHistoryParseError(entry.to_string()))?;

        match action {
            "BET_TO" | "RAISE_TO" => {
                // If there was a caller after a previous aggressive action, it's a squeeze.
                if n_aggressive >= 1 && had_caller_before_squeeze {
                    squeeze_candidate = true;
                }
                n_aggressive += 1;
                if first_aggressor_seat.is_none() {
                    first_aggressor_seat = Some(seat);
                }
                had_caller_before_squeeze = false;
            }
            "CALL" => {
                if n_aggressive >= 1 {
                    had_caller_before_squeeze = true;
                }
            }
            "FOLD" | "CHECK" => {}
            _ => {} // unknown actions ignored
        }

        // Only classify preflop actions (stop at FLOP/TURN/RIVER markers if present).
        if action == "FLOP" || action == "TURN" || action == "RIVER" {
            break;
        }
    }

    let pot_class = if squeeze_candidate {
        PotClass::Squeeze
    } else {
        match n_aggressive {
            0 => PotClass::Limped,
            1 => PotClass::Srp,
            2 => PotClass::ThreeBp,
            _ => PotClass::FourBp,
        }
    };

    Ok((pot_class, first_aggressor_seat))
}

// ─── Stack bucket ─────────────────────────────────────────────────────────────

pub fn stack_to_bucket(eff_bb: f64) -> StackBucket {
    if eff_bb <= 50.0        { StackBucket::S40 }
    else if eff_bb <= 80.0   { StackBucket::S60 }
    else if eff_bb <= 125.0  { StackBucket::S100 }
    else if eff_bb <= 175.0  { StackBucket::S150 }
    else                     { StackBucket::S200Plus }
}

// ─── Classification quality ───────────────────────────────────────────────────

fn classification_quality(
    pot_class:    PotClass,
    n_players:    u8,
    _stack_bucket: StackBucket,
    rake_profile: RakeProfile,
) -> ClassificationQuality {
    // Exact coverage: SRP, Limped, ThreeBp, or FourBp, 2-way, no rake, any stack bucket.
    //
    // The stack_bucket constraint was removed in Phase 2 to allow S40/S60/S150/S200+
    // artifacts to be served when present.  The artifact-key lookup will still miss
    // if no artifact exists for that specific stack bucket, so widening here is safe:
    // it only enables the EXACT *attempt*, not a guaranteed hit.
    //
    // Limped pots were added in Phase 3 (same gating logic: 2-way, norake).
    // ThreeBp was added in Phase 12 (same gating logic).
    // FourBp was added in Phase 14 with a dedicated 2-action [check, jam] menu
    // designed for low-SPR play (mean SPR 1.0 across the 4bp family).
    //
    // Note: board_bucket validity is enforced by the artifact_key lookup itself —
    // a None board_bucket (preflop_unknown) will produce a key with "preflop" as
    // the board component, which only matches if a preflop artifact actually exists.
    let is_exact = (pot_class == PotClass::Srp
                 || pot_class == PotClass::Limped
                 || pot_class == PotClass::ThreeBp
                 || pot_class == PotClass::FourBp)
        && n_players == 2
        && rake_profile == RakeProfile::NoRake;

    if is_exact {
        ClassificationQuality::Exact
    } else if n_players > 4 {
        // Very sparse coverage — flag as Unknown so EMERGENCY is triggered.
        ClassificationQuality::Unknown
    } else {
        ClassificationQuality::Approximate
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── seat_to_position ──────────────────────────────────────────────────────

    #[test]
    fn sixmax_positions() {
        // seats 0-5, button at index 0
        let cases = [
            (0, Position::Btn),
            (1, Position::Sb),
            (2, Position::Bb),
            (3, Position::Utg),
            (4, Position::Hj),
            (5, Position::Co),
        ];
        for (seat_idx, expected) in cases {
            assert_eq!(seat_to_position(seat_idx, 0, 6), expected, "seat_idx={seat_idx}");
        }
    }

    #[test]
    fn sixmax_positions_btn_at_index_3() {
        // Button in the middle of the table
        let btn_idx = 3;
        // offset = (seat - btn + 6) % 6
        assert_eq!(seat_to_position(3, btn_idx, 6), Position::Btn);
        assert_eq!(seat_to_position(4, btn_idx, 6), Position::Sb);
        assert_eq!(seat_to_position(5, btn_idx, 6), Position::Bb);
        assert_eq!(seat_to_position(0, btn_idx, 6), Position::Utg);
        assert_eq!(seat_to_position(1, btn_idx, 6), Position::Hj);
        assert_eq!(seat_to_position(2, btn_idx, 6), Position::Co);
    }

    #[test]
    fn heads_up_positions() {
        assert_eq!(seat_to_position(0, 0, 2), Position::Btn);
        assert_eq!(seat_to_position(1, 0, 2), Position::Bb);
    }

    // ── hero_is_oop ───────────────────────────────────────────────────────────

    #[test]
    fn bb_is_oop_vs_btn() {
        assert!(hero_is_oop(Position::Bb, Position::Btn));
    }

    #[test]
    fn btn_is_not_oop_vs_sb() {
        assert!(!hero_is_oop(Position::Btn, Position::Sb));
    }

    #[test]
    fn sb_is_oop_vs_bb() {
        assert!(hero_is_oop(Position::Sb, Position::Bb));
    }

    // ── stack_to_bucket ───────────────────────────────────────────────────────

    #[test]
    fn stack_buckets() {
        assert_eq!(stack_to_bucket(40.0),  StackBucket::S40);
        assert_eq!(stack_to_bucket(50.0),  StackBucket::S40);
        assert_eq!(stack_to_bucket(51.0),  StackBucket::S60);
        assert_eq!(stack_to_bucket(80.0),  StackBucket::S60);
        assert_eq!(stack_to_bucket(100.0), StackBucket::S100);
        assert_eq!(stack_to_bucket(125.0), StackBucket::S100);
        assert_eq!(stack_to_bucket(126.0), StackBucket::S150);
        assert_eq!(stack_to_bucket(175.0), StackBucket::S150);
        assert_eq!(stack_to_bucket(176.0), StackBucket::S200Plus);
        assert_eq!(stack_to_bucket(300.0), StackBucket::S200Plus);
    }

    // ── classify_spot ─────────────────────────────────────────────────────────

    fn six_seats() -> Vec<u8> { vec![1, 2, 3, 4, 5, 6] }

    #[test]
    fn srp_btn_vs_bb() {
        // BTN opens, everyone else folds, BB calls — SRP 2-way
        let history = vec!["4:BET_TO:250", "6:CALL"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 97.5,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(42),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, quality) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::Srp);
        assert_eq!(key.hero_pos, Position::Bb);
        assert_eq!(key.aggressor_pos, Some(Position::Btn));
        assert_eq!(key.n_players, 2);
        assert_eq!(key.stack_bucket, StackBucket::S100);
        assert_eq!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn srp_s40_is_exact() {
        // SRP at 40bb should also be Exact quality (gate widened in Phase 2).
        let history = vec!["4:BET_TO:250", "6:CALL"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 40.0,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(42),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, quality) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::Srp);
        assert_eq!(key.stack_bucket, StackBucket::S40);
        assert_eq!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn limped_pot_is_exact() {
        // Limped pot, 2-way, norake should be Exact quality (Phase 3).
        let history = vec!["4:CALL", "5:CALL", "6:CHECK"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 40.0,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(10),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, quality) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::Limped);
        assert_eq!(key.aggressor_pos, None);
        assert_eq!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn three_bet_pot() {
        // BTN opens, BB 3bets, BTN calls — hero is BTN (the opener who called)
        let history = vec!["4:BET_TO:250", "6:RAISE_TO:750", "4:CALL"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 4,
            street: Street::Flop,
            effective_stack_bb: 97.5,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(10),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, quality) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::ThreeBp);
        assert_eq!(key.hero_pos, Position::Btn);
        // Aggressor is the FIRST raiser (opener = BTN seat 4), not the 3-bettor.
        assert_eq!(key.aggressor_pos, Some(Position::Btn));
        // 3bp is Exact quality as of Phase 12.
        assert_eq!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn four_bet_pot_is_exact_2way_norake() {
        // BTN opens, BB 3bets, BTN 4bets, BB calls — 4bp, hero is BB
        let history = vec![
            "4:BET_TO:250", "6:RAISE_TO:750", "4:RAISE_TO:2000", "6:CALL",
        ];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 97.5,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(42),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, quality) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::FourBp);
        assert_eq!(key.hero_pos, Position::Bb);
        assert_eq!(key.aggressor_pos, Some(Position::Btn));
        assert_eq!(key.n_players, 2);
        // 4bp is Exact quality as of Phase 14.
        assert_eq!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn four_bet_pot_multiway_not_exact() {
        // 4bp but 3-way should NOT be Exact
        let history = vec![
            "4:BET_TO:250", "6:RAISE_TO:750", "4:RAISE_TO:2000", "6:CALL",
        ];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 97.5,
            n_players_in_hand: 3,
            action_history: &history,
            board_bucket: Some(42),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (_, quality) = classify_spot(&inp).unwrap();
        assert_ne!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn four_bet_pot_with_rake_not_exact() {
        // 4bp with rake should NOT be Exact
        let history = vec![
            "4:BET_TO:250", "6:RAISE_TO:750", "4:RAISE_TO:2000", "6:CALL",
        ];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 97.5,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(42),
            rake_profile_str: "rake5cap1bb",
            menu_version: 1,
        };
        let (_, quality) = classify_spot(&inp).unwrap();
        assert_ne!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn three_bet_pot_aggressor_is_opener() {
        // CO opens (seat 3), BB 3-bets (seat 6), CO calls — hero is BB (the 3-bettor)
        // Aggressor should be CO (the original opener), not BB (the 3-bettor).
        // This gives key "3bp/.../co_vs_bb_2way/..." with correct IP/OOP.
        let history = vec!["3:BET_TO:250", "6:RAISE_TO:750", "3:CALL"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 97.5,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(10),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, quality) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::ThreeBp);
        assert_eq!(key.hero_pos, Position::Bb);
        // Aggressor = CO (the opener, seat 3), NOT BB (the 3-bettor).
        assert_eq!(key.aggressor_pos, Some(Position::Co));
        assert_eq!(quality, ClassificationQuality::Exact);
    }

    #[test]
    fn limped_pot() {
        let history = vec!["4:CALL", "5:CALL", "6:CHECK"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 4,
            street: Street::Flop,
            effective_stack_bb: 100.0,
            n_players_in_hand: 3,
            action_history: &history,
            board_bucket: Some(0),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, _) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::Limped);
        assert_eq!(key.aggressor_pos, None);
    }

    #[test]
    fn squeeze_pot() {
        // BTN opens, CO calls, BB squeezes
        let history = vec!["4:BET_TO:250", "3:CALL", "6:RAISE_TO:900"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 4,
            street: Street::Flop,
            effective_stack_bb: 100.0,
            n_players_in_hand: 3,
            action_history: &history,
            board_bucket: Some(5),
            rake_profile_str: "norake",
            menu_version: 1,
        };
        let (key, _) = classify_spot(&inp).unwrap();
        assert_eq!(key.pot_class, PotClass::Squeeze);
    }

    #[test]
    fn unknown_rake_profile_errors() {
        let history = vec!["4:BET_TO:250", "6:CALL"];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 6,
            street: Street::Flop,
            effective_stack_bb: 100.0,
            n_players_in_hand: 2,
            action_history: &history,
            board_bucket: Some(0),
            rake_profile_str: "rake_unknown",
            menu_version: 1,
        };
        assert!(classify_spot(&inp).is_err());
    }

    #[test]
    fn hero_seat_not_found_errors() {
        let history: Vec<&str> = vec![];
        let inp = ClassifyInput {
            active_seats: &six_seats(),
            button_seat: 4,
            hero_seat: 99, // not in table
            street: Street::Preflop,
            effective_stack_bb: 100.0,
            n_players_in_hand: 6,
            action_history: &history,
            board_bucket: None,
            rake_profile_str: "norake",
            menu_version: 1,
        };
        assert!(classify_spot(&inp).is_err());
    }
}
