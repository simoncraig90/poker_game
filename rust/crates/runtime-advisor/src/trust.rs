//! Trust scoring for EXACT and EMERGENCY decisions.
//!
//! Trust is a `[0, 1]` scalar indicating confidence in the recommendation.
//! The overlay uses it to control visual treatment (bold vs dim, etc.).
//!
//! # Config
//! All thresholds are in `TrustConfig`.  Use `TrustConfig::default()` for
//! the baseline and override individual fields when loading from YAML.

use engine_core::HandBucket;

// ─── Config ──────────────────────────────────────────────────────────────────

pub struct TrustConfig {
    /// Base trust for EXACT decisions (before penalties).
    pub exact_base: f64,
    /// Trust deducted per 1% solver exploitability.
    pub exact_exploit_penalty_per_pct: f64,
    /// Trust deducted when the selected action has < `exact_mix_threshold` frequency.
    pub exact_mix_penalty: f64,
    /// Frequency below which the mix penalty applies.
    pub exact_mix_threshold: f32,
    /// Fixed trust tiers for EMERGENCY, indexed by `HandBucket::index()`.
    pub emergency_tiers: [f64; 12],
}

impl Default for TrustConfig {
    fn default() -> Self {
        Self {
            exact_base:                     0.95,
            exact_exploit_penalty_per_pct:  0.01,
            exact_mix_penalty:              0.10,
            exact_mix_threshold:            0.50,
            emergency_tiers: [
                0.70, // Monster
                0.65, // VeryStrong
                0.60, // Strong
                0.55, // StrongTwoPair
                0.50, // WeakTwoPair
                0.55, // Overpair
                0.50, // TopPairGoodKicker
                0.45, // TopPairWeak
                0.40, // WeakPair
                0.45, // StrongDraw
                0.35, // WeakDraw
                0.30, // Air
            ],
        }
    }
}

// ─── Scoring ─────────────────────────────────────────────────────────────────

/// Compute trust for an EXACT decision.
///
/// `exploitability_pct` comes from the artifact manifest (if present).
/// `action_probability` is the frequency of the chosen action from the strategy.
pub fn exact_trust(
    cfg:                 &TrustConfig,
    exploitability_pct:  Option<f64>,
    action_probability:  f32,
) -> f64 {
    let mut score = cfg.exact_base;
    if let Some(e) = exploitability_pct {
        score -= e * cfg.exact_exploit_penalty_per_pct;
    }
    if action_probability < cfg.exact_mix_threshold {
        score -= cfg.exact_mix_penalty;
    }
    score.clamp(0.0, 1.0)
}

/// Compute trust for an EMERGENCY decision.
pub fn emergency_trust(cfg: &TrustConfig, hand_bucket: HandBucket) -> f64 {
    cfg.emergency_tiers[hand_bucket.index()]
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use engine_core::HandBucket;

    #[test]
    fn exact_base_trust() {
        let cfg = TrustConfig::default();
        let t = exact_trust(&cfg, None, 0.80);
        assert!((t - 0.95).abs() < 1e-9);
    }

    #[test]
    fn exact_exploit_penalty() {
        let cfg = TrustConfig::default();
        // 5% exploitability → 5 * 0.01 = 0.05 penalty
        let t = exact_trust(&cfg, Some(5.0), 0.80);
        assert!((t - 0.90).abs() < 1e-9);
    }

    #[test]
    fn exact_mix_penalty() {
        let cfg = TrustConfig::default();
        // probability 0.3 < threshold 0.5 → 0.10 penalty
        let t = exact_trust(&cfg, None, 0.30);
        assert!((t - 0.85).abs() < 1e-9);
    }

    #[test]
    fn exact_both_penalties() {
        let cfg = TrustConfig::default();
        let t = exact_trust(&cfg, Some(5.0), 0.30);
        // 0.95 - 0.05 - 0.10 = 0.80
        assert!((t - 0.80).abs() < 1e-9);
    }

    #[test]
    fn exact_clamped_to_zero() {
        let cfg = TrustConfig::default();
        let t = exact_trust(&cfg, Some(100.0), 0.01);
        assert_eq!(t, 0.0);
    }

    #[test]
    fn emergency_monster_highest() {
        let cfg = TrustConfig::default();
        let t = emergency_trust(&cfg, HandBucket::Monster);
        assert!((t - 0.70).abs() < 1e-9);
    }

    #[test]
    fn emergency_air_lowest() {
        let cfg = TrustConfig::default();
        let t = emergency_trust(&cfg, HandBucket::Air);
        assert!((t - 0.30).abs() < 1e-9);
    }

    #[test]
    fn emergency_covers_all_buckets() {
        let cfg = TrustConfig::default();
        for i in 0u8..12 {
            let bucket: HandBucket = unsafe { std::mem::transmute(i) };
            let t = emergency_trust(&cfg, bucket);
            assert!(t >= 0.0 && t <= 1.0, "bucket {i} trust {t} out of range");
        }
    }
}
