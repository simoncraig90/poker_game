//! Mode routing: EXACT or EMERGENCY.
//!
//! Every request flows through `Router::recommend()`:
//!   1. Classify the spot -> (SpotKey, ClassificationQuality)
//!   2. If Exact quality AND artifact exists AND integrity passes -> EXACT
//!   3. Otherwise -> EMERGENCY
//!
//! The output of either path is always legalized before being returned.

use std::path::PathBuf;

use artifact_store::{
    load_manifest, verify_artifact,
    quarantine_artifact, is_quarantined, QuarantineReason,
};
use log::{info, warn};
use thiserror::Error;

use crate::action::{ActionKind, ActionMenuConfig, SizeContext, resolve_amount};
use crate::artifact_key::artifact_key;
use crate::classify::{ClassificationQuality, ClassifyError, ClassifyInput, SpotKey, classify_spot};
use crate::emergency::{EmergencyInput, decide_emergency};
use crate::emergency_range_prior::EmergencyRangePrior;
use crate::legalizer::{LegalizerInput, legalize};
use crate::recommend::{RecommendRequest, RecommendResponse, FocusHint, ExactDecision};
use crate::strategy::ExactStrategy;
use crate::trust::{TrustConfig, exact_trust, emergency_trust};

// ─── Mode ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Exact,
    Emergency,
}

// ─── Errors ──────────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum RouteError {
    #[error("classification failed: {0}")]
    Classify(#[from] ClassifyError),
}

// ─── Config ──────────────────────────────────────────────────────────────────

/// Runtime configuration for the mode router.
pub struct RouterConfig {
    /// Root directory containing solver artifacts, organized by artifact key.
    pub artifact_root: PathBuf,
    /// Directory for quarantined (failed integrity) artifacts.
    pub quarantine_dir: PathBuf,
    /// Expected artifact version for solver artifacts.
    pub expected_artifact_version: u32,
}

// ─── Router ──────────────────────────────────────────────────────────────────

/// The mode router.  Holds loaded prior, action menu config, trust config, and paths.
pub struct Router {
    pub config:       RouterConfig,
    pub prior:        EmergencyRangePrior,
    pub action_menu:  ActionMenuConfig,
    pub trust_config: TrustConfig,
}

impl Router {
    /// Produce a single action recommendation for the given request.
    ///
    /// Always returns a legal action — either EXACT or EMERGENCY.
    pub fn recommend(&self, req: &RecommendRequest) -> Result<RecommendResponse, RouteError> {
        // 1. Build ClassifyInput from owned request data.
        let action_strs: Vec<&str> = req.action_history.iter().map(|s| s.as_str()).collect();
        let classify_input = ClassifyInput {
            active_seats:       &req.active_seats,
            button_seat:        req.button_seat,
            hero_seat:          req.hero_seat,
            street:             req.street,
            effective_stack_bb: req.effective_stack_bb,
            n_players_in_hand:  req.n_players_in_hand,
            action_history:     &action_strs,
            board_bucket:       req.board_bucket,
            rake_profile_str:   &req.rake_profile_str,
            menu_version:       req.menu_version,
        };

        // 2. Classify.
        let (spot_key, quality) = classify_spot(&classify_input)?;

        // 3. Try EXACT.
        if quality == ClassificationQuality::Exact {
            if let Some(resp) = self.try_exact(&spot_key, quality, req) {
                return Ok(resp);
            }
        }

        // 4. EMERGENCY fallback.
        self.emergency_fallback(&spot_key, quality, req)
    }

    // ── EMERGENCY path ───────────────────────────────────────────────────────

    fn emergency_fallback(
        &self,
        spot_key: &SpotKey,
        quality:  ClassificationQuality,
        req:      &RecommendRequest,
    ) -> Result<RecommendResponse, RouteError> {
        let em_input = EmergencyInput {
            hole_cards:       req.hole_cards,
            board:            req.board.clone(),
            facing_bet:       req.facing_bet,
            pot:              req.pot,
            hero_committed:   req.hero_committed,
            hero_start_stack: req.hero_start_stack,
        };
        let em = decide_emergency(spot_key, &em_input, &self.prior);

        let legalized = legalize(&LegalizerInput {
            kind:          em.kind,
            target_amount: em.target_amount,
            legal_actions: req.legal_actions.clone(),
            hero_stack:    req.hero_stack,
            hero_committed: req.hero_committed,
        });

        info!(
            "EMERGENCY: equity={:.3} bucket={:?} -> {:?} (snapped={}) key={}",
            em.equity, em.hand_bucket, legalized.action.kind,
            legalized.was_snapped, artifact_key(spot_key),
        );

        let trust = emergency_trust(&self.trust_config, em.hand_bucket);

        Ok(RecommendResponse {
            mode:        Mode::Emergency,
            action:      legalized.action,
            was_snapped: legalized.was_snapped,
            snap_reason: legalized.snap_reason,
            spot_key:    spot_key.clone(),
            quality,
            focus_hint:  FocusHint::Emergency,
            emergency:   Some(em),
            exact:       None,
            trust_score: trust,
        })
    }

    // ── EXACT path ───────────────────────────────────────────────────────────

    fn try_exact(
        &self,
        spot_key: &SpotKey,
        quality:  ClassificationQuality,
        req:      &RecommendRequest,
    ) -> Option<RecommendResponse> {
        let key_str = artifact_key(spot_key);
        let artifact_dir  = self.config.artifact_root.join(&key_str);
        let bin_path      = artifact_dir.join("strategy.bin");
        let manifest_path = artifact_dir.join("strategy.manifest.json");

        // Skip if already quarantined.
        if is_quarantined(&bin_path, &self.config.quarantine_dir) {
            warn!("EXACT: artifact quarantined — key={}", key_str);
            return None;
        }

        // Check existence.
        if !bin_path.exists() || !manifest_path.exists() {
            return None;
        }

        // Load + verify manifest.
        let manifest = match load_manifest(&manifest_path) {
            Ok(m)  => m,
            Err(e) => {
                warn!("EXACT: manifest load failed — key={}: {}", key_str, e);
                let reason = QuarantineReason::from(&e);
                let _ = quarantine_artifact(&bin_path, reason, &self.config.quarantine_dir);
                return None;
            }
        };

        if let Err(e) = verify_artifact(&bin_path, &manifest, self.config.expected_artifact_version) {
            warn!("EXACT: integrity failed — key={}: {}", key_str, e);
            let reason = QuarantineReason::from(&e);
            let _ = quarantine_artifact(&bin_path, reason, &self.config.quarantine_dir);
            return None;
        }

        // Parse strategy binary.
        let bytes = match std::fs::read(&bin_path) {
            Ok(b) => b,
            Err(e) => {
                warn!("EXACT: read failed — key={}: {}", key_str, e);
                return None;
            }
        };

        let strategy = match ExactStrategy::parse(&bytes) {
            Ok(s) => s,
            Err(e) => {
                warn!("EXACT: strategy parse failed — key={}: {}", key_str, e);
                let _ = quarantine_artifact(
                    &bin_path,
                    QuarantineReason::StrategyParseError,
                    &self.config.quarantine_dir,
                );
                return None;
            }
        };

        // Evaluate hand bucket.
        let hand_bucket = engine_core::evaluate(req.hole_cards, &req.board);

        // Select action.
        let selected = strategy.select(hand_bucket);

        // Resolve concrete amount for bet/raise sizes.
        let target_amount = match selected.kind {
            ActionKind::Fold | ActionKind::Check | ActionKind::Call | ActionKind::Jam => 0.0,
            ActionKind::BetTo | ActionKind::RaiseTo => {
                let size_ctx = SizeContext {
                    pot:                req.pot,
                    big_blind:          req.big_blind,
                    hero_position:      spot_key.hero_pos.as_str().to_string(),
                    facing_open_amount: req.facing_open_amount,
                    facing_3bet_amount: req.facing_3bet_amount,
                    facing_bet_amount:  req.facing_bet_amount,
                    hero_stack:         req.hero_stack,
                };
                resolve_amount(selected.size_id, &size_ctx, &self.action_menu)
                    .unwrap_or(0.0)
            }
        };

        // Legalize.
        let legalized = legalize(&LegalizerInput {
            kind:           selected.kind,
            target_amount,
            legal_actions:  req.legal_actions.clone(),
            hero_stack:     req.hero_stack,
            hero_committed: req.hero_committed,
        });

        info!(
            "EXACT: bucket={:?} action={:?}@{:.1}% -> {:?} amt={:.0} (snapped={}) key={}",
            hand_bucket, selected.kind, selected.probability * 100.0,
            legalized.action.kind, legalized.action.amount,
            legalized.was_snapped, key_str,
        );

        let trust = exact_trust(&self.trust_config, None, selected.probability);

        Some(RecommendResponse {
            mode:        Mode::Exact,
            action:      legalized.action,
            was_snapped: legalized.was_snapped,
            snap_reason: legalized.snap_reason,
            spot_key:    spot_key.clone(),
            quality,
            focus_hint:  FocusHint::Exact,
            emergency:   None,
            exact:       Some(ExactDecision {
                hand_bucket,
                chosen_kind:     selected.kind,
                chosen_size:     selected.size_id,
                probability:     selected.probability,
                resolved_amount: target_amount,
            }),
            trust_score: trust,
        })
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::action::{AbstractSizeId, ActionKind, ActionMenuConfig, Street};
    use crate::emergency_range_prior::*;
    use crate::legalizer::LiveLegalAction;
    use crate::recommend::RecommendRequest;
    use crate::strategy::{ActionEntry, build_strategy_binary};
    use engine_core::{HandBucket, parse_card};
    use std::path::Path;
    use tempfile::tempdir;

    // ── Helpers ──────────────────────────────────────────────────────────────

    fn build_prior(fill: f64) -> (tempfile::TempDir, EmergencyRangePrior) {
        use artifact_store::checksum_bytes;

        let dir = tempdir().unwrap();
        let bin_path      = dir.path().join("prior.bin");
        let manifest_path = dir.path().join("prior.manifest.json");

        let data: Vec<u8> = (0..TABLE_LEN)
            .flat_map(|_| fill.to_le_bytes())
            .collect();
        std::fs::write(&bin_path, &data).unwrap();

        let manifest = serde_json::json!({
            "artifact_type":    "emergency_range_prior",
            "version":           1u32,
            "checksum_sha256":   checksum_bytes(&data),
            "file_size_bytes":   data.len() as u64,
            "n_hand_buckets":    N_HAND_BUCKETS,
            "n_board_textures":  N_BOARD_TEXTURES,
            "n_pot_classes":     N_POT_CLASSES,
            "n_aggressor_roles": N_AGGRESSOR_ROLES,
            "n_player_buckets":  N_PLAYER_BUCKETS,
        });
        std::fs::write(&manifest_path, serde_json::to_string_pretty(&manifest).unwrap()).unwrap();
        let prior = EmergencyRangePrior::load(&bin_path, &manifest_path).unwrap();
        (dir, prior)
    }

    fn test_action_menu() -> ActionMenuConfig {
        let yaml = r#"
version: "v1"
menu_version: 1
preflop_opens:
  OPEN_STD:  { utg: 2.2, hj: 2.2, co: 2.2, btn: 2.5, sb: 3.0 }
  OPEN_LARGE: { utg: 3.0, hj: 3.0, co: 3.0, btn: 3.0, sb: 4.0 }
preflop_3bets:
  THREEBET_IP_STD: 3.2
  THREEBET_OOP_STD: 4.0
  THREEBET_BB_WIDE: 4.5
preflop_4bets:
  FOURBET_STD: 2.3
postflop_pot_fractions:
  CBET_SMALL: 0.33
  CBET_MEDIUM: 0.60
  CBET_LARGE: 0.80
  CBET_OVERBET: 1.25
  PROTECTION_VALUE_SM: 0.33
postflop_raise_multipliers:
  RAISE_VS_SMALL: 3.0
  RAISE_VS_LARGE: 2.5
jam_threshold_of_stack: 0.95
"#;
        let cfg: ActionMenuConfig = serde_yaml::from_str(yaml).unwrap();
        cfg.validate().unwrap();
        cfg
    }

    fn make_router(prior: EmergencyRangePrior) -> (tempfile::TempDir, tempfile::TempDir, Router) {
        let artifact_root  = tempdir().unwrap();
        let quarantine_dir = tempdir().unwrap();
        let config = RouterConfig {
            artifact_root:              artifact_root.path().to_path_buf(),
            quarantine_dir:             quarantine_dir.path().to_path_buf(),
            expected_artifact_version:  1,
        };
        let router = Router {
            config, prior,
            action_menu: test_action_menu(),
            trust_config: TrustConfig::default(),
        };
        (artifact_root, quarantine_dir, router)
    }

    // ── Test strategy artifact ───────────────────────────────────────────────

    /// 3-action strategy: Check, BetTo(CbetSmall), BetTo(CbetMedium)
    fn test_actions() -> Vec<ActionEntry> {
        vec![
            ActionEntry { kind: ActionKind::Check, size_id: AbstractSizeId::None },
            ActionEntry { kind: ActionKind::BetTo, size_id: AbstractSizeId::CbetSmall },
            ActionEntry { kind: ActionKind::BetTo, size_id: AbstractSizeId::CbetMedium },
        ]
    }

    fn test_matrix() -> Vec<Vec<f32>> {
        vec![
            vec![0.0, 0.0, 1.0],   // Monster: bet 60%
            vec![0.0, 0.2, 0.8],   // VeryStrong
            vec![0.0, 0.7, 0.3],   // Strong
            vec![0.1, 0.6, 0.3],   // StrongTwoPair
            vec![0.3, 0.5, 0.2],   // WeakTwoPair
            vec![0.1, 0.5, 0.4],   // Overpair
            vec![0.2, 0.6, 0.2],   // TopPairGoodKicker: CbetSmall (0.6)
            vec![0.4, 0.5, 0.1],   // TopPairWeak
            vec![0.6, 0.3, 0.1],   // WeakPair
            vec![0.3, 0.5, 0.2],   // StrongDraw
            vec![0.7, 0.2, 0.1],   // WeakDraw
            vec![0.8, 0.1, 0.1],   // Air: check
        ]
    }

    /// Write a valid strategy artifact at the given key path under artifact_root.
    fn write_artifact(artifact_root: &Path, key: &str, actions: &[ActionEntry], matrix: &[Vec<f32>]) {
        use artifact_store::checksum_bytes;

        let dir = artifact_root.join(key);
        std::fs::create_dir_all(&dir).unwrap();

        let bin_data = build_strategy_binary(actions, matrix);
        std::fs::write(dir.join("strategy.bin"), &bin_data).unwrap();

        let checksum = checksum_bytes(&bin_data);
        let manifest = serde_json::json!({
            "artifact_type":   "solver_strategy",
            "version":          1u32,
            "checksum_sha256":  checksum,
            "file_size_bytes":  bin_data.len() as u64,
            "menu_version":     1u8,
            "n_actions":        actions.len(),
            "n_hand_buckets":   12,
            "scenario_id":      key,
        });
        std::fs::write(
            dir.join("strategy.manifest.json"),
            serde_json::to_string_pretty(&manifest).unwrap(),
        ).unwrap();
    }

    // ── Request builder ──────────────────────────────────────────────────────

    /// Standard SRP BTN-vs-BB flop request.
    /// Hero: As Kd on Ah 7c 2s → TopPairGoodKicker.
    /// Artifact key: srp/flop/btn_vs_bb_2way/s100/bb42/norake/mv1
    fn make_request() -> RecommendRequest {
        RecommendRequest {
            active_seats:       vec![1, 2, 3, 4, 5, 6],
            button_seat:        4,
            hero_seat:          6,
            street:             Street::Flop,
            effective_stack_bb: 97.5,
            n_players_in_hand:  2,
            action_history:     vec!["4:BET_TO:250".into(), "6:CALL".into()],
            board_bucket:       Some(42),
            rake_profile_str:   "norake".into(),
            menu_version:       1,
            hole_cards:         [parse_card("As").unwrap(), parse_card("Kd").unwrap()],
            board:              vec![
                parse_card("Ah").unwrap(),
                parse_card("7c").unwrap(),
                parse_card("2s").unwrap(),
            ],
            facing_bet:         false,
            pot:                550.0,
            big_blind:          100.0,
            hero_committed:     250.0,
            hero_start_stack:   10000.0,
            hero_stack:         9750.0,
            legal_actions:      vec![
                LiveLegalAction { kind: ActionKind::Check, min_amount: 0.0, max_amount: 0.0 },
                LiveLegalAction { kind: ActionKind::BetTo, min_amount: 100.0, max_amount: 9750.0 },
            ],
            facing_open_amount: 0.0,
            facing_3bet_amount: 0.0,
            facing_bet_amount:  0.0,
        }
    }

    const EXPECTED_KEY: &str = "srp/flop/btn_vs_bb_2way/s100/bb42/norake/mv1";

    // ── Golden test 1: EXACT hit ─────────────────────────────────────────────

    #[test]
    fn golden_exact_hit() {
        let (_pdir, prior) = build_prior(0.50);
        let (adir, _qdir, router) = make_router(prior);

        write_artifact(adir.path(), EXPECTED_KEY, &test_actions(), &test_matrix());

        let resp = router.recommend(&make_request()).unwrap();

        assert_eq!(resp.mode, Mode::Exact, "should route to EXACT");
        assert_eq!(resp.focus_hint, FocusHint::Exact);
        assert!(resp.exact.is_some());
        assert!(resp.emergency.is_none());

        let exact = resp.exact.unwrap();
        assert_eq!(exact.hand_bucket, HandBucket::TopPairGoodKicker);
        assert_eq!(exact.chosen_kind, ActionKind::BetTo);
        assert_eq!(exact.chosen_size, AbstractSizeId::CbetSmall);
        assert!((exact.probability - 0.6).abs() < 1e-5);

        // CbetSmall = 0.33 * pot = 0.33 * 550 = 181.5
        assert!((exact.resolved_amount - 181.5).abs() < 1.0);

        // Legalized action should be BetTo (within legal range [100, 9750]).
        assert_eq!(resp.action.kind, ActionKind::BetTo);
        assert!((resp.action.amount - 181.5).abs() < 1.0);
        assert!(!resp.was_snapped);
    }

    // ── Golden test 2: corruption quarantines + EMERGENCY ────────────────────

    #[test]
    fn golden_corruption_quarantines() {
        let (_pdir, prior) = build_prior(0.50);
        let (adir, qdir, router) = make_router(prior);

        write_artifact(adir.path(), EXPECTED_KEY, &test_actions(), &test_matrix());

        // Corrupt one byte in strategy.bin.
        let bin_path = adir.path().join(EXPECTED_KEY).join("strategy.bin");
        let mut data = std::fs::read(&bin_path).unwrap();
        data[0] ^= 0xFF;
        std::fs::write(&bin_path, &data).unwrap();

        let resp = router.recommend(&make_request()).unwrap();

        assert_eq!(resp.mode, Mode::Emergency, "corrupted artifact should fall back to EMERGENCY");
        assert!(resp.emergency.is_some());
        assert!(resp.exact.is_none());
        assert_eq!(resp.focus_hint, FocusHint::Emergency);

        // Artifact should have been quarantined.
        assert!(is_quarantined(&bin_path, qdir.path()));
    }

    // ── Golden test 3: cache miss -> EMERGENCY ───────────────────────────────

    #[test]
    fn golden_cache_miss() {
        let (_pdir, prior) = build_prior(0.50);
        let (_adir, _qdir, router) = make_router(prior);

        // No artifact written.
        let resp = router.recommend(&make_request()).unwrap();

        assert_eq!(resp.mode, Mode::Emergency);
        assert!(resp.emergency.is_some());
        assert!(resp.exact.is_none());
    }

    // ── Golden test 4: all outputs remain legal ──────────────────────────────

    #[test]
    fn golden_outputs_are_legal() {
        let (_pdir, prior) = build_prior(0.50);
        let (adir, _qdir, router) = make_router(prior);
        write_artifact(adir.path(), EXPECTED_KEY, &test_actions(), &test_matrix());

        // EXACT path.
        let resp = router.recommend(&make_request()).unwrap();
        assert!(
            resp.action.kind == ActionKind::Check
                || resp.action.kind == ActionKind::BetTo
                || resp.action.kind == ActionKind::Jam,
            "EXACT action {:?} not in legal set", resp.action.kind
        );
        if resp.action.kind == ActionKind::BetTo {
            assert!(resp.action.amount >= 100.0, "bet below min");
            assert!(resp.action.amount <= 9750.0, "bet above max");
        }

        // EMERGENCY path (no artifact for 3bp scenario).
        let mut req = make_request();
        req.action_history = vec!["4:BET_TO:250".into(), "6:RAISE_TO:750".into(), "4:CALL".into()];
        req.board_bucket = Some(10);
        req.legal_actions = vec![
            LiveLegalAction { kind: ActionKind::Fold,    min_amount: 0.0,   max_amount: 0.0 },
            LiveLegalAction { kind: ActionKind::Call,    min_amount: 500.0, max_amount: 500.0 },
        ];
        req.facing_bet = true;

        let resp = router.recommend(&req).unwrap();
        assert_eq!(resp.mode, Mode::Emergency);
        assert!(
            resp.action.kind == ActionKind::Fold || resp.action.kind == ActionKind::Call,
            "EMERGENCY action {:?} not in legal set", resp.action.kind
        );
    }

    // ── Golden test 5: deterministic ─────────────────────────────────────────

    #[test]
    fn golden_deterministic() {
        let (_pdir, prior) = build_prior(0.50);
        let (adir, _qdir, router) = make_router(prior);
        write_artifact(adir.path(), EXPECTED_KEY, &test_actions(), &test_matrix());

        let req = make_request();
        let r1 = router.recommend(&req).unwrap();
        let r2 = router.recommend(&req).unwrap();

        assert_eq!(r1.mode, r2.mode);
        assert_eq!(r1.action.kind, r2.action.kind);
        assert!((r1.action.amount - r2.action.amount).abs() < 1e-9);
        assert_eq!(r1.was_snapped, r2.was_snapped);
        assert_eq!(r1.focus_hint, r2.focus_hint);
    }

    // ── Structural tests (adapted from previous suite) ───────────────────────

    #[test]
    fn non_exact_quality_skips_exact() {
        let (_pdir, prior) = build_prior(0.50);
        let (adir, _qdir, router) = make_router(prior);

        // Write artifact that would match if quality were Exact.
        // But 5-way gives Unknown quality (n_players > 4), so EXACT is never tried.
        write_artifact(
            adir.path(),
            "srp/flop/btn_vs_bb_5way/s100/bb42/norake/mv1",
            &test_actions(),
            &test_matrix(),
        );

        let mut req = make_request();
        req.n_players_in_hand = 5;

        let resp = router.recommend(&req).unwrap();
        assert_eq!(resp.mode, Mode::Emergency);
    }

    #[test]
    fn fourbp_2way_norake_uses_exact() {
        let (_pdir, prior) = build_prior(0.50);
        let (adir, _qdir, router) = make_router(prior);

        // 4bp 2-way norake is Exact as of Phase 14.
        // Use a 2-action [check, jam] artifact.
        let fourbp_actions = vec![
            ActionEntry { kind: ActionKind::Check, size_id: AbstractSizeId::None },
            ActionEntry { kind: ActionKind::Jam,   size_id: AbstractSizeId::None },
        ];
        let fourbp_matrix: Vec<Vec<f32>> = vec![
            vec![0.00, 1.00],  // Monster
            vec![0.00, 1.00],  // VeryStrong
            vec![0.05, 0.95],  // Strong
            vec![0.10, 0.90],  // StrongTwoPair
            vec![0.25, 0.75],  // WeakTwoPair
            vec![0.05, 0.95],  // Overpair
            vec![0.30, 0.70],  // TopPairGoodKicker
            vec![0.55, 0.45],  // TopPairWeak
            vec![0.75, 0.25],  // WeakPair
            vec![0.40, 0.60],  // StrongDraw
            vec![0.85, 0.15],  // WeakDraw
            vec![0.85, 0.15],  // Air
        ];

        // BTN opens, BB 3bets, BTN 4bets, BB calls — hero is BB (seat 6)
        let key = "4bp/flop/btn_vs_bb_2way/s100/bb42/norake/mv1";
        write_artifact(adir.path(), key, &fourbp_actions, &fourbp_matrix);

        let mut req = make_request();
        req.action_history = vec![
            "4:BET_TO:250".into(), "6:RAISE_TO:750".into(),
            "4:RAISE_TO:2000".into(), "6:CALL".into(),
        ];
        req.hero_seat = 6;
        req.board_bucket = Some(42);
        req.legal_actions = vec![
            LiveLegalAction { kind: ActionKind::Check, min_amount: 0.0, max_amount: 0.0 },
            LiveLegalAction { kind: ActionKind::BetTo, min_amount: 100.0, max_amount: 9750.0 },
        ];
        req.facing_bet = false;

        let resp = router.recommend(&req).unwrap();
        assert_eq!(resp.mode, Mode::Exact, "4bp 2-way norake should route to EXACT");
        assert!(resp.exact.is_some());

        let exact = resp.exact.unwrap();
        // AsKd on Ah7c2s = TopPairGoodKicker → row [0.30, 0.70] → Jam (index 1)
        assert_eq!(exact.chosen_kind, ActionKind::Jam);
        assert!((exact.probability - 0.70).abs() < 1e-5);
    }

    #[test]
    fn strategy_parse_error_quarantines() {
        let (_pdir, prior) = build_prior(0.50);
        let (adir, qdir, router) = make_router(prior);

        // Write artifact with valid checksum but invalid strategy binary content.
        let dir = adir.path().join(EXPECTED_KEY);
        std::fs::create_dir_all(&dir).unwrap();

        let bad_bin = b"STRTgarbage that is long enough to pass size check but fails parse";
        let checksum = artifact_store::checksum_bytes(bad_bin);
        std::fs::write(dir.join("strategy.bin"), bad_bin).unwrap();

        let manifest = serde_json::json!({
            "artifact_type":   "solver_strategy",
            "version":          1u32,
            "checksum_sha256":  checksum,
            "file_size_bytes":  bad_bin.len() as u64,
        });
        std::fs::write(
            dir.join("strategy.manifest.json"),
            serde_json::to_string_pretty(&manifest).unwrap(),
        ).unwrap();

        let resp = router.recommend(&make_request()).unwrap();
        assert_eq!(resp.mode, Mode::Emergency);

        let bin_path = dir.join("strategy.bin");
        assert!(is_quarantined(&bin_path, qdir.path()));
    }

    #[test]
    fn emergency_decision_carries_equity() {
        let (_pdir, prior) = build_prior(0.50);
        let (_adir, _qdir, router) = make_router(prior);

        let resp = router.recommend(&make_request()).unwrap();
        let em = resp.emergency.unwrap();
        assert!(em.equity >= 0.0 && em.equity <= 1.0);
    }
}
