//! Fast hand evaluator: (hole_cards, board) → HandBucket.
//!
//! This module is intentionally pure and deterministic:
//! - No allocation on the hot path beyond the small Vec in `evaluate`.
//! - No trust score, no EV, no range reasoning — just hand-vs-board bucketing.
//! - Output feeds the emergency range prior lookup; imprecision is acceptable
//!   because we're bucketing, not running exact CFR.

use thiserror::Error;

// ─── Public types ─────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum EvalError {
    #[error("invalid card string {0:?} — expected rank (2-9,T,J,Q,K,A) + suit (c,d,h,s)")]
    InvalidCard(String),
}

/// Rank (2–14) and suit (0–3) representation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Card {
    pub rank: u8, // 2=2 … 14=A
    pub suit: u8, // 0=c 1=d 2=h 3=s
}

/// Coarse hand strength bucket. 12 variants, repr u8 so it can be used as
/// a table index. Ordered strongest→weakest.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[repr(u8)]
pub enum HandBucket {
    Monster           = 0,  // quads, straight flush
    VeryStrong        = 1,  // full house, nut flush
    Strong            = 2,  // non-nut flush, straight, set/trips
    StrongTwoPair     = 3,  // top two, overpair+board pair
    WeakTwoPair       = 4,  // any weaker two-pair
    Overpair          = 5,  // pocket pair above all board ranks
    TopPairGoodKicker = 6,  // top pair kicker ≥ J
    TopPairWeak       = 7,  // top pair weak kicker or middle pair
    WeakPair          = 8,  // bottom pair, underpair, board pair only
    StrongDraw        = 9,  // nut FD, OESD, or OESD+FD
    WeakDraw          = 10, // FD, gutshot, backdoor combos
    Air               = 11, // no pair, no draw on this street
}

impl HandBucket {
    pub const COUNT: usize = 12;

    pub fn index(self) -> usize { self as usize }
}

/// Coarse board texture for the emergency prior lookup.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum BoardTexture {
    DryRainbow       = 0, // unpaired, rainbow, low connectivity  (K72r)
    DryPaired        = 1, // one pair on board                    (AA2, 772)
    ConnectedRainbow = 2, // consecutive ranks, no FD             (QJTr, 987r)
    FlushdrawBoard   = 3, // two-tone, modest connectivity        (K72fd)
    WetConnected     = 4, // connected + two-tone                 (T98s, J98fd)
    Monotone         = 5, // all same suit                        (K72m)
}

impl BoardTexture {
    pub const COUNT: usize = 6;

    pub fn index(self) -> usize { self as usize }
}

// ─── Card parsing ─────────────────────────────────────────────────────────────

pub fn parse_card(s: &str) -> Result<Card, EvalError> {
    let s = s.trim();
    if s.len() < 2 || s.len() > 2 {
        return Err(EvalError::InvalidCard(s.to_string()));
    }
    let rank_ch = s.chars().next().unwrap().to_ascii_uppercase();
    let suit_ch = s.chars().nth(1).unwrap().to_ascii_lowercase();

    let rank = match rank_ch {
        '2'..='9' => rank_ch as u8 - b'0',
        'T'       => 10,
        'J'       => 11,
        'Q'       => 12,
        'K'       => 13,
        'A'       => 14,
        _         => return Err(EvalError::InvalidCard(s.to_string())),
    };
    let suit = match suit_ch {
        'c' => 0,
        'd' => 1,
        'h' => 2,
        's' => 3,
        _   => return Err(EvalError::InvalidCard(s.to_string())),
    };
    Ok(Card { rank, suit })
}

// ─── Main evaluation entry point ─────────────────────────────────────────────

/// Evaluate hole cards against a board and return the coarsest HandBucket.
///
/// Board can be 0 cards (preflop), 3 (flop), 4 (turn), or 5 (river).
/// Preflop uses a simple rank-based heuristic; postflop does full analysis.
pub fn evaluate(hole: [Card; 2], board: &[Card]) -> HandBucket {
    if board.len() < 3 {
        return evaluate_preflop(hole);
    }

    let all: Vec<Card> = [hole[0], hole[1]]
        .iter()
        .chain(board.iter())
        .copied()
        .collect();

    // ── Straight flush / Quads → Monster ─────────────────────────────────────
    if has_straight_flush(&all) {
        return HandBucket::Monster;
    }
    let counts = rank_counts(&all);
    if counts.iter().any(|&c| c >= 4) {
        return HandBucket::Monster;
    }

    // ── Full house → VeryStrong ───────────────────────────────────────────────
    let trip_count = counts.iter().filter(|&&c| c >= 3).count();
    let pair_count = counts.iter().filter(|&&c| c >= 2).count();
    if trip_count >= 2 || (trip_count >= 1 && pair_count >= 2) {
        return HandBucket::VeryStrong;
    }

    // ── Flush → VeryStrong (nut) or Strong ───────────────────────────────────
    if let Some(suit) = flush_suit(&all) {
        return if is_nut_flush(hole, board, suit) {
            HandBucket::VeryStrong
        } else {
            HandBucket::Strong
        };
    }

    // ── Straight → Strong ─────────────────────────────────────────────────────
    if has_straight_rank_mask(rank_mask(&all)) {
        return HandBucket::Strong;
    }

    // ── Trips/Set → Strong ────────────────────────────────────────────────────
    if trip_count >= 1 {
        return HandBucket::Strong;
    }

    // ── Pair analysis ─────────────────────────────────────────────────────────
    let pair_ranks: Vec<u8> = (2u8..=14).filter(|&r| counts[r as usize] >= 2).collect();

    if pair_ranks.len() >= 2 {
        return classify_two_pair(hole, board, &pair_ranks);
    }

    if pair_ranks.len() == 1 {
        return classify_single_pair(hole, board, pair_ranks[0]);
    }

    // ── No pair → draws or air ────────────────────────────────────────────────
    classify_draws(hole, board)
}

// ─── Preflop heuristic ────────────────────────────────────────────────────────

fn evaluate_preflop(hole: [Card; 2]) -> HandBucket {
    let hi = hole[0].rank.max(hole[1].rank);
    let lo = hole[0].rank.min(hole[1].rank);
    let suited = hole[0].suit == hole[1].suit;

    // Pocket pairs
    if hi == lo {
        return if hi >= 12 { HandBucket::Overpair }       // QQ+
               else if hi >= 8  { HandBucket::TopPairGoodKicker } // 88-JJ
               else              { HandBucket::WeakPair };
    }

    // Broadway / strong aces
    if hi == 14 && lo >= 12 { return HandBucket::TopPairGoodKicker; } // AK, AQ
    if hi == 14 && lo >= 10 { return HandBucket::TopPairWeak; }       // AJ, AT
    if hi >= 13 && lo >= 11 { return HandBucket::TopPairWeak; }       // KQ, KJ

    if suited { HandBucket::WeakDraw } else { HandBucket::Air }
}

// ─── Two-pair classification ──────────────────────────────────────────────────

fn classify_two_pair(hole: [Card; 2], board: &[Card], pair_ranks: &[u8]) -> HandBucket {
    let board_counts = rank_counts(board);
    let board_top = board.iter().map(|c| c.rank).max().unwrap_or(0);

    // Hero "contributes" to a pair if their hole card matches a board card rank.
    let hero_contributed: Vec<u8> = pair_ranks
        .iter()
        .filter(|&&r| {
            (hole[0].rank == r || hole[1].rank == r) && board_counts[r as usize] >= 1
        })
        .copied()
        .collect();

    if hero_contributed.is_empty() {
        // Both pairs come from the board alone — hero doesn't hold either.
        return HandBucket::WeakPair;
    }

    let hero_max = *hero_contributed.iter().max().unwrap();
    if hero_max >= board_top {
        HandBucket::StrongTwoPair
    } else {
        HandBucket::WeakTwoPair
    }
}

// ─── Single-pair classification ───────────────────────────────────────────────

fn classify_single_pair(hole: [Card; 2], board: &[Card], pair_rank: u8) -> HandBucket {
    let board_counts = rank_counts(board);
    let board_sorted: Vec<u8> = {
        let mut v: Vec<u8> = board.iter().map(|c| c.rank).collect();
        v.sort_unstable_by(|a, b| b.cmp(a));
        v.dedup();
        v
    };
    let board_top = board_sorted.first().copied().unwrap_or(0);

    // Pocket pair (both hole cards same rank)?
    if hole[0].rank == hole[1].rank {
        return if pair_rank > board_top { HandBucket::Overpair }
               else if pair_rank == board_top { HandBucket::TopPairGoodKicker }
               else { HandBucket::WeakPair };
    }

    // One hole card pairs the board?
    if board_counts[pair_rank as usize] >= 1 {
        let kicker = if hole[0].rank == pair_rank { hole[1].rank } else { hole[0].rank };

        if pair_rank == board_top {
            return if kicker >= 11 { HandBucket::TopPairGoodKicker } else { HandBucket::TopPairWeak };
        }
        let second_board = board_sorted.get(1).copied().unwrap_or(0);
        if pair_rank == second_board {
            return HandBucket::TopPairWeak; // middle pair
        }
        return HandBucket::WeakPair; // bottom pair
    }

    // Board has a pair, hero doesn't contribute.
    HandBucket::WeakPair
}

// ─── Draw classification ──────────────────────────────────────────────────────

fn classify_draws(hole: [Card; 2], board: &[Card]) -> HandBucket {
    // On the river, draws are missed — they're now air.
    if board.len() >= 5 {
        return HandBucket::Air;
    }

    let all: Vec<Card> = [hole[0], hole[1]].iter().chain(board).copied().collect();

    // ── Flush draws ───────────────────────────────────────────────────────────
    let mut has_nut_fd = false;
    let mut has_fd = false;

    for suit in 0u8..4 {
        let cards_of_suit: Vec<&Card> = all.iter().filter(|c| c.suit == suit).collect();
        if cards_of_suit.len() == 4 {
            let hero_in = hole.iter().any(|h| h.suit == suit);
            if hero_in {
                has_fd = true;
                let hero_max = hole.iter().filter(|h| h.suit == suit).map(|h| h.rank).max().unwrap_or(0);
                if hero_max == 14 {
                    has_nut_fd = true;
                }
            }
        }
    }

    // ── Straight draws (hero must contribute at least one rank) ───────────────
    let board_rank_mask = rank_mask(board);
    let hero_rank_mask  = rank_mask(&hole);
    let combined = board_rank_mask | hero_rank_mask;
    // Include ace-low bit
    let combined_with_low = if combined & (1 << 14) != 0 { combined | (1 << 1) } else { combined };
    let hero_with_low     = if hero_rank_mask & (1 << 14) != 0 { hero_rank_mask | (1 << 1) } else { hero_rank_mask };

    let mut has_oesd = false;
    let mut has_gutshot = false;

    // OESD: 4 consecutive ranks, window offset 2..=9 (excludes wheel/Broadway terminus)
    for low in 2u16..=9 {
        let w4 = 0xFu16 << low;
        if (combined_with_low & w4) == w4 && (hero_with_low & w4) != 0 {
            has_oesd = true;
            break;
        }
    }

    if !has_oesd {
        // Gutshot: 4-of-5 consecutive ranks, with exactly one gap
        for low in 1u16..=10 {
            let w5 = 0x1Fu16 << low;
            let hits = (combined_with_low & w5).count_ones();
            if hits == 4 && (hero_with_low & w5) != 0 {
                has_gutshot = true;
                break;
            }
        }
    }

    match (has_nut_fd, has_fd || has_oesd, has_gutshot) {
        (true, _, _) => HandBucket::StrongDraw,
        (_, true, _) => HandBucket::StrongDraw,
        (_, _, true) => HandBucket::WeakDraw,
        _            => HandBucket::Air,
    }
}

// ─── Board texture classification ────────────────────────────────────────────

/// Classify the texture of the first 3 board cards (flop).
/// Turn/river texture assessment delegates to the flop for simplicity.
pub fn classify_board_texture(board: &[Card]) -> BoardTexture {
    if board.len() < 3 {
        return BoardTexture::DryRainbow;
    }
    let flop = &board[..3];

    // Suit counts
    let mut suit_counts = [0u8; 4];
    for c in flop { suit_counts[c.suit as usize] += 1; }
    let max_suited = *suit_counts.iter().max().unwrap();

    if max_suited == 3 {
        return BoardTexture::Monotone;
    }

    // Rank analysis
    let mut ranks: Vec<u8> = flop.iter().map(|c| c.rank).collect();
    ranks.sort_unstable();
    let has_board_pair = ranks[0] == ranks[1] || ranks[1] == ranks[2];

    if has_board_pair {
        return BoardTexture::DryPaired;
    }

    // Connectivity: gap between highest and lowest rank of flop
    let gap = ranks[2] - ranks[0];
    let connected = gap <= 4; // within a 5-rank window
    let has_fd = max_suited == 2;

    match (connected, has_fd) {
        (true,  true)  => BoardTexture::WetConnected,
        (true,  false) => BoardTexture::ConnectedRainbow,
        (false, true)  => BoardTexture::FlushdrawBoard,
        (false, false) => BoardTexture::DryRainbow,
    }
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

fn rank_counts(cards: &[Card]) -> [u8; 15] {
    let mut c = [0u8; 15];
    for card in cards { c[card.rank as usize] += 1; }
    c
}

/// Bitmask: bit i is set if rank i appears in cards.
fn rank_mask(cards: &[Card]) -> u16 {
    let mut m = 0u16;
    for c in cards { m |= 1u16 << c.rank; }
    m
}

fn flush_suit(cards: &[Card]) -> Option<u8> {
    let mut sc = [0u8; 4];
    for c in cards { sc[c.suit as usize] += 1; }
    sc.iter().enumerate().find(|(_, &n)| n >= 5).map(|(s, _)| s as u8)
}

fn is_nut_flush(hole: [Card; 2], board: &[Card], suit: u8) -> bool {
    // Nut flush: hero holds the highest-rank card of the flush suit among all cards.
    let hero_max = hole.iter().filter(|c| c.suit == suit).map(|c| c.rank).max();
    let all_max  = hole.iter().chain(board.iter()).filter(|c| c.suit == suit).map(|c| c.rank).max();
    matches!((hero_max, all_max), (Some(h), Some(a)) if h == a)
}

/// Returns true if 5 consecutive bits are set in the rank bitmask (ace-low included).
fn has_straight_rank_mask(mask: u16) -> bool {
    let m = if mask & (1 << 14) != 0 { mask | (1 << 1) } else { mask };
    for low in 1u16..=10 {
        if (m >> low) & 0x1F == 0x1F { return true; }
    }
    false
}

fn has_straight_flush(cards: &[Card]) -> bool {
    for suit in 0u8..4 {
        let suited: Vec<Card> = cards.iter().filter(|c| c.suit == suit).copied().collect();
        if suited.len() >= 5 && has_straight_rank_mask(rank_mask(&suited)) {
            return true;
        }
    }
    false
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn c(s: &str) -> Card { parse_card(s).unwrap() }
    fn board(ss: &[&str]) -> Vec<Card> { ss.iter().map(|s| c(s)).collect() }

    // ── parse_card ────────────────────────────────────────────────────────────

    #[test]
    fn parse_valid_cards() {
        assert_eq!(c("Ah").rank, 14);
        assert_eq!(c("Ah").suit, 2);
        assert_eq!(c("2c").rank, 2);
        assert_eq!(c("2c").suit, 0);
        assert_eq!(c("Ts").rank, 10);
        assert_eq!(c("Kd").rank, 13);
    }

    #[test]
    fn parse_invalid_card_errors() {
        assert!(parse_card("XX").is_err());
        assert!(parse_card("Ax").is_err());
        assert!(parse_card("").is_err());
    }

    // ── HandBucket ordering ───────────────────────────────────────────────────

    #[test]
    fn bucket_ordering_monster_strongest() {
        assert!(HandBucket::Monster < HandBucket::Air);
        assert!(HandBucket::VeryStrong < HandBucket::Overpair);
    }

    // ── Specific hand evaluations ─────────────────────────────────────────────

    #[test]
    fn aces_full_is_very_strong() {
        let hole = [c("Ac"), c("Ad")];
        let b = board(&["As", "Kh", "Kd"]);
        assert_eq!(evaluate(hole, &b), HandBucket::VeryStrong); // full house
    }

    #[test]
    fn quad_aces_is_monster() {
        let hole = [c("Ac"), c("Ad")];
        let b = board(&["As", "Ah", "2c"]);
        assert_eq!(evaluate(hole, &b), HandBucket::Monster);
    }

    #[test]
    fn nut_flush_is_very_strong() {
        let hole = [c("Ah"), c("2h")];
        let b = board(&["Kh", "7h", "3h"]);
        assert_eq!(evaluate(hole, &b), HandBucket::VeryStrong); // nut flush (Ace)
    }

    #[test]
    fn non_nut_flush_is_strong() {
        let hole = [c("Jh"), c("2h")];
        let b = board(&["Kh", "7h", "3h"]);
        // Flush present (all hearts) but hero doesn't hold the Ace
        // Wait: K is on board, hero has J - K is the highest flush card
        // Hero has Jh, board has Kh = Kh is higher → not nut flush
        assert_eq!(evaluate(hole, &b), HandBucket::Strong);
    }

    #[test]
    fn straight_is_strong() {
        let hole = [c("9c"), c("8d")];
        let b = board(&["7h", "6c", "5s"]);
        assert_eq!(evaluate(hole, &b), HandBucket::Strong); // 9-high straight
    }

    #[test]
    fn set_is_strong() {
        let hole = [c("7c"), c("7d")];
        let b = board(&["7h", "Kc", "2s"]);
        assert_eq!(evaluate(hole, &b), HandBucket::Strong); // set of 7s
    }

    #[test]
    fn top_two_pair_is_strong_two_pair() {
        let hole = [c("Kc"), c("Qd")];
        let b = board(&["Kh", "Qc", "2s"]);
        assert_eq!(evaluate(hole, &b), HandBucket::StrongTwoPair);
    }

    #[test]
    fn weak_two_pair_bottom_two() {
        let hole = [c("2c"), c("3d")];
        let b = board(&["Ah", "2h", "3s"]);
        // Hero has 33 + 22 two pair, but A is on board = board top is A, hero doesn't pair A
        assert_eq!(evaluate(hole, &b), HandBucket::WeakTwoPair);
    }

    #[test]
    fn overpair_above_all_board_cards() {
        let hole = [c("Ac"), c("Ad")];
        let b = board(&["Kh", "7c", "2s"]);
        assert_eq!(evaluate(hole, &b), HandBucket::Overpair);
    }

    #[test]
    fn top_pair_good_kicker() {
        let hole = [c("Kc"), c("Qd")];
        let b = board(&["Kh", "7c", "2s"]);
        assert_eq!(evaluate(hole, &b), HandBucket::TopPairGoodKicker); // KQ top pair with Q kicker ≥ J
    }

    #[test]
    fn top_pair_weak_kicker() {
        let hole = [c("Kc"), c("4d")];
        let b = board(&["Kh", "7c", "2s"]);
        assert_eq!(evaluate(hole, &b), HandBucket::TopPairWeak); // K4 top pair, 4 kicker < J
    }

    #[test]
    fn middle_pair_is_top_pair_weak() {
        let hole = [c("7c"), c("2d")];
        let b = board(&["Kh", "7d", "2s"]);
        // Actually hero has two pair here (7+2 both pair board)
        // Let's use a proper middle pair hand
        let hole2 = [c("7c"), c("6d")];
        let b2 = board(&["Kh", "7d", "3s"]);
        assert_eq!(evaluate(hole2, &b2), HandBucket::TopPairWeak); // middle pair (7 is second-high)
    }

    #[test]
    fn nut_flush_draw_is_strong_draw() {
        let hole = [c("Ah"), c("2c")]; // Ah = nut flush draw on heart board
        let b = board(&["Kh", "7h", "3c"]); // two hearts + one non-heart
        // 3 hearts in play: Ah, Kh, 7h — that's 3 suited, not a draw yet
        // Let's use 4-to-flush scenario
        let hole3 = [c("Ah"), c("2d")];
        let b3 = board(&["Kh", "7h", "3h"]);
        // Now we have Ah, Kh, 7h, 3h = 4 hearts → flush! Not a draw.
        // For a draw: hole has Ah, board has 3 hearts total = 4 hearts → but that's a made flush
        // Actually flush check comes first; let me set up a true FD:
        let hole4 = [c("Ah"), c("2d")];
        let b4 = board(&["Kh", "7h", "3c"]); // only 2 hearts on board + Ah = 3 total = draw
        // 3 hearts total: Ah (hole), Kh, 7h (board) = 3 suited, need 4 for draw
        let b5 = board(&["Kh", "7h", "3h"]); // 4 hearts: Ah + 3 board hearts → flush! caught earlier
        // To get a nut FD (4-to-flush without completing): Ah in hole, 3 board hearts
        // For 4 hearts without 5: hole[Ah, Xnonheart] + board[Xh, Xh, Xnonheart] = 3 hearts total (not a draw with 4)
        // We need 4 total: hole[Ah, Xh] + board[Xh, Xh, Xnon] = 4 hearts = flush draw
        let hole5 = [c("Ah"), c("2h")]; // both hearts
        let b6 = board(&["Kh", "7h", "3c"]); // 2 more hearts on board = 4 total hearts
        // Total hearts: Ah, 2h, Kh, 7h = 4 → flush draw (not 5)
        assert_eq!(evaluate(hole5, &b6), HandBucket::StrongDraw); // nut FD (Ah)
    }

    #[test]
    fn oesd_is_strong_draw() {
        let hole = [c("9c"), c("8d")];
        let b = board(&["7h", "6c", "2s"]); // 9876 = OESD
        assert_eq!(evaluate(hole, &b), HandBucket::StrongDraw);
    }

    #[test]
    fn gutshot_is_weak_draw() {
        let hole = [c("9c"), c("5d")];
        let b = board(&["8h", "7c", "2s"]); // 9_87 = gutshot (needs 6)
        assert_eq!(evaluate(hole, &b), HandBucket::WeakDraw);
    }

    #[test]
    fn air_on_dry_board() {
        let hole = [c("9c"), c("2d")];
        let b = board(&["Ah", "Kc", "5s"]); // no pair, no draw
        assert_eq!(evaluate(hole, &b), HandBucket::Air);
    }

    #[test]
    fn draws_on_river_are_air() {
        // 4-to-flush on river = missed draw = air
        let hole = [c("Ah"), c("2h")];
        let b = board(&["Kh", "7h", "3c", "4d", "Js"]); // 5-card board, 4 hearts but no flush
        // Actually Ah, 2h, Kh, 7h = 4 hearts, not 5 → the flush draw missed
        // But wait, classify_draws checks board.len() >= 5 → Air
        assert_eq!(evaluate(hole, &b), HandBucket::Air);
    }

    // ── Board texture ─────────────────────────────────────────────────────────

    #[test]
    fn board_texture_dry_rainbow() {
        let b = board(&["Kh", "7d", "2c"]);
        assert_eq!(classify_board_texture(&b), BoardTexture::DryRainbow);
    }

    #[test]
    fn board_texture_monotone() {
        let b = board(&["Kh", "7h", "2h"]);
        assert_eq!(classify_board_texture(&b), BoardTexture::Monotone);
    }

    #[test]
    fn board_texture_dry_paired() {
        let b = board(&["Kh", "Kd", "2c"]);
        assert_eq!(classify_board_texture(&b), BoardTexture::DryPaired);
    }

    #[test]
    fn board_texture_connected_rainbow() {
        let b = board(&["Qh", "Jd", "Tc"]);
        assert_eq!(classify_board_texture(&b), BoardTexture::ConnectedRainbow);
    }

    #[test]
    fn board_texture_flush_draw_board() {
        let b = board(&["Kh", "7h", "2c"]); // 2 hearts, not connected
        assert_eq!(classify_board_texture(&b), BoardTexture::FlushdrawBoard);
    }

    #[test]
    fn board_texture_wet_connected() {
        let b = board(&["Th", "9h", "8c"]); // connected + 2 hearts
        assert_eq!(classify_board_texture(&b), BoardTexture::WetConnected);
    }

    #[test]
    fn board_texture_preflop_is_dry_rainbow() {
        assert_eq!(classify_board_texture(&[]), BoardTexture::DryRainbow);
    }
}
