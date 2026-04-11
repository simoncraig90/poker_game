//! Solver strategy artifact: binary format, parser, and action selection.
//!
//! # Binary format v1
//!
//! All values little-endian.
//!
//! ```text
//! Offset       Size            Field
//! 0..4         4               magic: b"STRT"
//! 4..8         4               version: u32 = 1
//! 8..10        2               n_actions: u16
//! 10..12       2               n_hand_buckets: u16 = 12
//! 12..16       4               reserved: [0u8; 4]
//! 16..16+2N    2*N             action_table: [(kind_wire: u8, size_wire: u8)]
//! 16+2N..end   4*12*N          strategy_matrix: f32[12][N] (row-major)
//! ```
//!
//! Total: 16 + 50*N bytes.
//!
//! # Action selection
//!
//! For a given hand bucket, pick the action with the highest probability.
//! Ties broken by lowest action index (deterministic).

use crate::action::{AbstractSizeId, ActionKind};
use engine_core::HandBucket;
use thiserror::Error;

// ─── Constants ──────────────────────────────────────────────────────────────

pub const MAGIC: &[u8; 4] = b"STRT";
pub const FORMAT_VERSION: u32 = 1;
pub const N_HAND_BUCKETS: usize = 12;

const HEADER_SIZE: usize = 16;
const ROW_SUM_TOLERANCE: f32 = 0.02;

// ─── Types ──────────────────────────────────────────────────────────────────

/// One entry in the strategy's action table.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ActionEntry {
    pub kind:    ActionKind,
    pub size_id: AbstractSizeId,
}

/// Result of selecting an action for a specific hand bucket.
#[derive(Debug, Clone)]
pub struct SelectedAction {
    pub kind:        ActionKind,
    pub size_id:     AbstractSizeId,
    pub probability: f32,
}

// ─── Errors ─────────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum StrategyLoadError {
    #[error("bad magic: expected STRT")]
    BadMagic,

    #[error("unsupported version {0}")]
    UnsupportedVersion(u32),

    #[error("action entry {index}: unknown kind={kind} size={size}")]
    BadActionEntry { index: usize, kind: u8, size: u8 },

    #[error("{field}: expected {expected}, got {actual}")]
    BadDimension { field: &'static str, expected: usize, actual: usize },

    #[error("hand bucket {bucket}: row sum {sum} not ~1.0 or ~0.0")]
    ProbabilityError { bucket: usize, sum: f32 },

    #[error("binary too short: expected {expected} bytes, got {actual}")]
    TooShort { expected: usize, actual: usize },

    #[error("no actions in strategy")]
    Empty,
}

// ─── ExactStrategy ──────────────────────────────────────────────────────────

#[derive(Debug)]
pub struct ExactStrategy {
    actions:   Vec<ActionEntry>,
    matrix:    Vec<f32>, // row-major: [hand_bucket][action_idx]
}

impl ExactStrategy {
    /// Parse a strategy binary from raw bytes.
    pub fn parse(bytes: &[u8]) -> Result<Self, StrategyLoadError> {
        // ── Header ───────────────────────────────────────────────────────────
        if bytes.len() < HEADER_SIZE {
            return Err(StrategyLoadError::TooShort {
                expected: HEADER_SIZE,
                actual:   bytes.len(),
            });
        }

        if &bytes[0..4] != MAGIC {
            return Err(StrategyLoadError::BadMagic);
        }

        let version = u32::from_le_bytes(bytes[4..8].try_into().unwrap());
        if version != FORMAT_VERSION {
            return Err(StrategyLoadError::UnsupportedVersion(version));
        }

        let n_actions = u16::from_le_bytes(bytes[8..10].try_into().unwrap()) as usize;
        let n_buckets = u16::from_le_bytes(bytes[10..12].try_into().unwrap()) as usize;

        if n_actions == 0 {
            return Err(StrategyLoadError::Empty);
        }
        if n_buckets != N_HAND_BUCKETS {
            return Err(StrategyLoadError::BadDimension {
                field:    "n_hand_buckets",
                expected: N_HAND_BUCKETS,
                actual:   n_buckets,
            });
        }

        let expected_size = HEADER_SIZE + 2 * n_actions + 4 * N_HAND_BUCKETS * n_actions;
        if bytes.len() < expected_size {
            return Err(StrategyLoadError::TooShort {
                expected: expected_size,
                actual:   bytes.len(),
            });
        }

        // ── Action table ─────────────────────────────────────────────────────
        let mut actions = Vec::with_capacity(n_actions);
        for i in 0..n_actions {
            let off = HEADER_SIZE + 2 * i;
            let kind_wire = bytes[off];
            let size_wire = bytes[off + 1];

            let kind = ActionKind::from_wire_u8(kind_wire).ok_or(
                StrategyLoadError::BadActionEntry { index: i, kind: kind_wire, size: size_wire },
            )?;
            let size_id = AbstractSizeId::from_wire_u8(size_wire).ok_or(
                StrategyLoadError::BadActionEntry { index: i, kind: kind_wire, size: size_wire },
            )?;

            actions.push(ActionEntry { kind, size_id });
        }

        // ── Strategy matrix ──────────────────────────────────────────────────
        let matrix_start = HEADER_SIZE + 2 * n_actions;
        let n_floats = N_HAND_BUCKETS * n_actions;
        let mut matrix = Vec::with_capacity(n_floats);
        for i in 0..n_floats {
            let off = matrix_start + 4 * i;
            let val = f32::from_le_bytes(bytes[off..off + 4].try_into().unwrap());
            matrix.push(val);
        }

        // ── Validate row sums ────────────────────────────────────────────────
        for h in 0..N_HAND_BUCKETS {
            let row_start = h * n_actions;
            let sum: f32 = matrix[row_start..row_start + n_actions].iter().sum();
            let valid = sum.abs() < ROW_SUM_TOLERANCE           // all zeros
                || (sum - 1.0).abs() < ROW_SUM_TOLERANCE;       // sums to ~1.0
            if !valid {
                return Err(StrategyLoadError::ProbabilityError { bucket: h, sum });
            }
        }

        Ok(ExactStrategy { actions, matrix })
    }

    /// Select the highest-probability action for a hand bucket.
    ///
    /// Ties broken by lowest action index (deterministic).
    pub fn select(&self, hand_bucket: HandBucket) -> SelectedAction {
        let n = self.actions.len();
        let row_start = hand_bucket.index() * n;
        let row = &self.matrix[row_start..row_start + n];

        let mut best_idx = 0;
        let mut best_prob = row[0];
        for (i, &p) in row.iter().enumerate().skip(1) {
            if p > best_prob {
                best_idx = i;
                best_prob = p;
            }
        }

        SelectedAction {
            kind:        self.actions[best_idx].kind,
            size_id:     self.actions[best_idx].size_id,
            probability: best_prob,
        }
    }

    pub fn n_actions(&self) -> usize       { self.actions.len() }
    pub fn actions(&self)   -> &[ActionEntry] { &self.actions }
}

// ─── Builder (for tests and tooling) ─────────────────────────────────────────

/// Build a valid strategy binary from action entries and a probability matrix.
///
/// `matrix` is `[hand_bucket_0, hand_bucket_1, ..., hand_bucket_11]` where
/// each inner Vec has `actions.len()` f32 probabilities.
pub fn build_strategy_binary(actions: &[ActionEntry], matrix: &[Vec<f32>]) -> Vec<u8> {
    let n = actions.len();
    assert_eq!(matrix.len(), N_HAND_BUCKETS, "matrix must have {N_HAND_BUCKETS} rows");
    for (i, row) in matrix.iter().enumerate() {
        assert_eq!(row.len(), n, "row {i} has {} cols, expected {n}", row.len());
    }

    let total = HEADER_SIZE + 2 * n + 4 * N_HAND_BUCKETS * n;
    let mut buf = Vec::with_capacity(total);

    // Header
    buf.extend_from_slice(MAGIC);
    buf.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    buf.extend_from_slice(&(n as u16).to_le_bytes());
    buf.extend_from_slice(&(N_HAND_BUCKETS as u16).to_le_bytes());
    buf.extend_from_slice(&[0u8; 4]);

    // Action table
    for a in actions {
        buf.push(a.kind.to_wire_u8());
        buf.push(a.size_id.to_wire_u8());
    }

    // Strategy matrix
    for row in matrix {
        for &p in row {
            buf.extend_from_slice(&p.to_le_bytes());
        }
    }

    assert_eq!(buf.len(), total);
    buf
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use engine_core::HandBucket;

    fn three_action_entries() -> Vec<ActionEntry> {
        vec![
            ActionEntry { kind: ActionKind::Check, size_id: AbstractSizeId::None },
            ActionEntry { kind: ActionKind::BetTo, size_id: AbstractSizeId::CbetSmall },
            ActionEntry { kind: ActionKind::BetTo, size_id: AbstractSizeId::CbetMedium },
        ]
    }

    /// 12 rows, each summing to 1.0.
    fn valid_matrix_3() -> Vec<Vec<f32>> {
        vec![
            vec![0.0, 0.0, 1.0],   // Monster: always bet big
            vec![0.0, 0.2, 0.8],   // VeryStrong
            vec![0.0, 0.7, 0.3],   // Strong
            vec![0.1, 0.6, 0.3],   // StrongTwoPair
            vec![0.3, 0.5, 0.2],   // WeakTwoPair
            vec![0.1, 0.5, 0.4],   // Overpair
            vec![0.2, 0.6, 0.2],   // TopPairGoodKicker
            vec![0.4, 0.5, 0.1],   // TopPairWeak
            vec![0.6, 0.3, 0.1],   // WeakPair
            vec![0.3, 0.5, 0.2],   // StrongDraw
            vec![0.7, 0.2, 0.1],   // WeakDraw
            vec![0.8, 0.1, 0.1],   // Air
        ]
    }

    #[test]
    fn parse_valid_strategy() {
        let bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        let strat = ExactStrategy::parse(&bin).unwrap();
        assert_eq!(strat.n_actions(), 3);
        assert_eq!(strat.actions()[0].kind, ActionKind::Check);
        assert_eq!(strat.actions()[1].size_id, AbstractSizeId::CbetSmall);
    }

    #[test]
    fn round_trip_builder_parser() {
        let actions = three_action_entries();
        let matrix = valid_matrix_3();
        let bin = build_strategy_binary(&actions, &matrix);
        let strat = ExactStrategy::parse(&bin).unwrap();

        for (h, row) in matrix.iter().enumerate() {
            let n = actions.len();
            for (a, &expected) in row.iter().enumerate() {
                let actual = strat.matrix[h * n + a];
                assert!((actual - expected).abs() < 1e-6, "mismatch at [{h}][{a}]");
            }
        }
    }

    #[test]
    fn select_picks_highest_probability() {
        let bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        let strat = ExactStrategy::parse(&bin).unwrap();

        // Monster: [0.0, 0.0, 1.0] → CbetMedium
        let sel = strat.select(HandBucket::Monster);
        assert_eq!(sel.kind, ActionKind::BetTo);
        assert_eq!(sel.size_id, AbstractSizeId::CbetMedium);
        assert!((sel.probability - 1.0).abs() < 1e-6);

        // Air: [0.8, 0.1, 0.1] → Check
        let sel = strat.select(HandBucket::Air);
        assert_eq!(sel.kind, ActionKind::Check);
        assert!((sel.probability - 0.8).abs() < 1e-6);

        // TopPairGoodKicker: [0.2, 0.6, 0.2] → CbetSmall
        let sel = strat.select(HandBucket::TopPairGoodKicker);
        assert_eq!(sel.kind, ActionKind::BetTo);
        assert_eq!(sel.size_id, AbstractSizeId::CbetSmall);
        assert!((sel.probability - 0.6).abs() < 1e-6);
    }

    #[test]
    fn select_breaks_ties_by_lowest_index() {
        let actions = vec![
            ActionEntry { kind: ActionKind::Check, size_id: AbstractSizeId::None },
            ActionEntry { kind: ActionKind::BetTo, size_id: AbstractSizeId::CbetSmall },
        ];
        // All rows: [0.5, 0.5] — tied; lowest index (Check) should win.
        let matrix: Vec<Vec<f32>> = (0..12).map(|_| vec![0.5, 0.5]).collect();
        let bin = build_strategy_binary(&actions, &matrix);
        let strat = ExactStrategy::parse(&bin).unwrap();

        for h in 0..12 {
            let bucket: HandBucket = unsafe { std::mem::transmute(h as u8) };
            let sel = strat.select(bucket);
            assert_eq!(sel.kind, ActionKind::Check, "tie should go to index 0 for bucket {h}");
        }
    }

    #[test]
    fn all_zero_row_is_valid() {
        let actions = three_action_entries();
        let mut matrix = valid_matrix_3();
        matrix[0] = vec![0.0, 0.0, 0.0]; // Monster row all zeros (impossible hand)
        let bin = build_strategy_binary(&actions, &matrix);
        let strat = ExactStrategy::parse(&bin).unwrap();

        // select on all-zero row returns first action
        let sel = strat.select(HandBucket::Monster);
        assert_eq!(sel.kind, ActionKind::Check);
        assert!((sel.probability - 0.0).abs() < 1e-6);
    }

    #[test]
    fn bad_magic_fails() {
        let mut bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        bin[0] = b'X';
        assert!(matches!(ExactStrategy::parse(&bin), Err(StrategyLoadError::BadMagic)));
    }

    #[test]
    fn wrong_version_fails() {
        let mut bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        bin[4..8].copy_from_slice(&2u32.to_le_bytes());
        assert!(matches!(ExactStrategy::parse(&bin), Err(StrategyLoadError::UnsupportedVersion(2))));
    }

    #[test]
    fn wrong_n_hand_buckets_fails() {
        let mut bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        bin[10..12].copy_from_slice(&8u16.to_le_bytes()); // not 12
        assert!(matches!(ExactStrategy::parse(&bin), Err(StrategyLoadError::BadDimension { .. })));
    }

    #[test]
    fn bad_action_kind_fails() {
        let mut bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        bin[HEADER_SIZE] = 99; // invalid kind wire
        assert!(matches!(ExactStrategy::parse(&bin), Err(StrategyLoadError::BadActionEntry { .. })));
    }

    #[test]
    fn bad_probability_sum_fails() {
        let actions = three_action_entries();
        let mut matrix = valid_matrix_3();
        matrix[5] = vec![0.5, 0.5, 0.5]; // sums to 1.5
        let bin = build_strategy_binary(&actions, &matrix);
        assert!(matches!(ExactStrategy::parse(&bin), Err(StrategyLoadError::ProbabilityError { bucket: 5, .. })));
    }

    #[test]
    fn truncated_binary_fails() {
        let bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        let short = &bin[..bin.len() - 10];
        assert!(matches!(ExactStrategy::parse(short), Err(StrategyLoadError::TooShort { .. })));
    }

    #[test]
    fn empty_actions_fails() {
        let mut buf = Vec::new();
        buf.extend_from_slice(MAGIC);
        buf.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
        buf.extend_from_slice(&0u16.to_le_bytes()); // n_actions = 0
        buf.extend_from_slice(&(N_HAND_BUCKETS as u16).to_le_bytes());
        buf.extend_from_slice(&[0u8; 4]);
        assert!(matches!(ExactStrategy::parse(&buf), Err(StrategyLoadError::Empty)));
    }

    #[test]
    fn binary_size_is_correct() {
        let bin = build_strategy_binary(&three_action_entries(), &valid_matrix_3());
        // 16 + 50*3 = 166
        assert_eq!(bin.len(), 16 + 50 * 3);
    }
}
