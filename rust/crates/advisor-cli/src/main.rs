//! advisor-cli — JSON stdin/stdout bridge to the runtime-advisor Router.
//!
//! Reads JSONL from stdin (one RecommendRequest per line), writes JSONL to
//! stdout (one response per line).  Matches the subprocess pattern used by
//! solver_bridge.py.
//!
//! Usage:
//!   advisor-cli --artifact-root PATH --action-menu PATH \
//!               --prior-bin PATH --prior-manifest PATH   \
//!               [--quarantine-dir PATH]

use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::time::Instant;

use engine_core::parse_card;
use runtime_advisor::action::{
    AbstractSizeId, ActionKind, ActionMenuConfig, Street,
};
use runtime_advisor::artifact_key::artifact_key;
use runtime_advisor::classify::ClassificationQuality;
use runtime_advisor::emergency_range_prior::EmergencyRangePrior;
use runtime_advisor::legalizer::{LiveLegalAction, SnapReason};
use runtime_advisor::mode::{Mode, RouteError, Router, RouterConfig};
use runtime_advisor::recommend::{FocusHint, RecommendRequest, RecommendResponse};
use runtime_advisor::trust::TrustConfig;
use serde::{Deserialize, Serialize};

// ─── JSON input types ────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct JsonRequest {
    active_seats:       Vec<u8>,
    button_seat:        u8,
    hero_seat:          u8,
    street:             String,
    effective_stack_bb: f64,
    n_players_in_hand:  u8,
    action_history:     Vec<String>,
    board_bucket:       Option<u8>,
    rake_profile:       String,
    menu_version:       u8,
    hole_cards:         [String; 2],
    board:              Vec<String>,
    facing_bet:         bool,
    pot:                f64,
    big_blind:          f64,
    hero_committed:     f64,
    hero_start_stack:   f64,
    hero_stack:         f64,
    legal_actions:      Vec<JsonLegalAction>,
    #[serde(default)]
    facing_open_amount: f64,
    #[serde(default)]
    facing_3bet_amount: f64,
    #[serde(default)]
    facing_bet_amount:  f64,
}

#[derive(Deserialize)]
struct JsonLegalAction {
    kind: String,
    min:  f64,
    max:  f64,
}

// ─── JSON output types ───────────────────────────────────────────────────────

#[derive(Serialize)]
struct JsonResponse {
    mode:           String,
    action_kind:    String,
    action_amount:  f64,
    was_snapped:    bool,
    snap_reason:    String,
    focus_hint:     String,
    quality:        String,
    artifact_key:   String,
    trust_score:    f64,
    latency_us:     u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    exact:          Option<JsonExact>,
    #[serde(skip_serializing_if = "Option::is_none")]
    emergency:      Option<JsonEmergency>,
}

#[derive(Serialize)]
struct JsonExact {
    hand_bucket:     String,
    chosen_kind:     String,
    chosen_size:     String,
    probability:     f32,
    resolved_amount: f64,
}

#[derive(Serialize)]
struct JsonEmergency {
    equity:        f64,
    hand_bucket:   String,
    board_texture: String,
    kind:          String,
    target_amount: f64,
}

#[derive(Serialize)]
struct JsonError {
    error: String,
}

// ─── String conversions ──────────────────────────────────────────────────────

fn street_from_str(s: &str) -> Street {
    match s {
        "preflop" => Street::Preflop,
        "flop"    => Street::Flop,
        "turn"    => Street::Turn,
        "river"   => Street::River,
        other     => { eprintln!("warn: unknown street '{other}', defaulting to Flop"); Street::Flop }
    }
}

fn action_kind_from_str(s: &str) -> ActionKind {
    match s {
        "fold"     => ActionKind::Fold,
        "check"    => ActionKind::Check,
        "call"     => ActionKind::Call,
        "bet_to"   => ActionKind::BetTo,
        "raise_to" => ActionKind::RaiseTo,
        "jam"      => ActionKind::Jam,
        other      => { eprintln!("warn: unknown action '{other}', defaulting to Fold"); ActionKind::Fold }
    }
}

fn action_kind_str(k: ActionKind) -> &'static str {
    match k {
        ActionKind::Fold    => "fold",
        ActionKind::Check   => "check",
        ActionKind::Call    => "call",
        ActionKind::BetTo   => "bet_to",
        ActionKind::RaiseTo => "raise_to",
        ActionKind::Jam     => "jam",
    }
}

fn snap_reason_str(r: SnapReason) -> &'static str {
    match r {
        SnapReason::NoSnap                 => "no_snap",
        SnapReason::FoldToCheck            => "fold_to_check",
        SnapReason::BelowMinimum           => "below_minimum",
        SnapReason::AboveMaximum           => "above_maximum",
        SnapReason::NearJam                => "near_jam",
        SnapReason::KindNotLegal           => "kind_not_legal",
        SnapReason::MultipleEntriesSelected => "multiple_entries_selected",
    }
}

fn mode_str(m: Mode) -> &'static str {
    match m { Mode::Exact => "exact", Mode::Emergency => "emergency" }
}

fn focus_hint_str(f: FocusHint) -> &'static str {
    match f { FocusHint::Exact => "exact", FocusHint::Emergency => "emergency" }
}

fn quality_str(q: ClassificationQuality) -> &'static str {
    match q {
        ClassificationQuality::Exact       => "exact",
        ClassificationQuality::Approximate => "approximate",
        ClassificationQuality::Unknown     => "unknown",
    }
}

fn hand_bucket_str(hb: engine_core::HandBucket) -> &'static str {
    match hb {
        engine_core::HandBucket::Monster            => "monster",
        engine_core::HandBucket::VeryStrong          => "very_strong",
        engine_core::HandBucket::Strong              => "strong",
        engine_core::HandBucket::StrongTwoPair       => "strong_two_pair",
        engine_core::HandBucket::WeakTwoPair         => "weak_two_pair",
        engine_core::HandBucket::Overpair            => "overpair",
        engine_core::HandBucket::TopPairGoodKicker   => "top_pair_good_kicker",
        engine_core::HandBucket::TopPairWeak         => "top_pair_weak",
        engine_core::HandBucket::WeakPair            => "weak_pair",
        engine_core::HandBucket::StrongDraw          => "strong_draw",
        engine_core::HandBucket::WeakDraw            => "weak_draw",
        engine_core::HandBucket::Air                 => "air",
    }
}

fn board_texture_str(bt: engine_core::BoardTexture) -> &'static str {
    match bt {
        engine_core::BoardTexture::DryRainbow       => "dry_rainbow",
        engine_core::BoardTexture::DryPaired        => "dry_paired",
        engine_core::BoardTexture::ConnectedRainbow => "connected_rainbow",
        engine_core::BoardTexture::FlushdrawBoard   => "flushdraw_board",
        engine_core::BoardTexture::WetConnected     => "wet_connected",
        engine_core::BoardTexture::Monotone         => "monotone",
    }
}

fn size_id_str(s: AbstractSizeId) -> &'static str {
    match s {
        AbstractSizeId::None              => "none",
        AbstractSizeId::OpenStd           => "open_std",
        AbstractSizeId::OpenLarge         => "open_large",
        AbstractSizeId::ThreebetIpStd     => "threebet_ip_std",
        AbstractSizeId::ThreebetOopStd    => "threebet_oop_std",
        AbstractSizeId::ThreebetBbWide    => "threebet_bb_wide",
        AbstractSizeId::FourbetStd        => "fourbet_std",
        AbstractSizeId::CbetSmall         => "cbet_small",
        AbstractSizeId::CbetMedium        => "cbet_medium",
        AbstractSizeId::CbetLarge         => "cbet_large",
        AbstractSizeId::CbetOverbet       => "cbet_overbet",
        AbstractSizeId::RaiseVsSmall      => "raise_vs_small",
        AbstractSizeId::RaiseVsLarge      => "raise_vs_large",
        AbstractSizeId::ProtectionValueSm => "protection_value_sm",
    }
}

// ─── Request conversion ──────────────────────────────────────────────────────

fn convert_request(jr: &JsonRequest) -> Result<RecommendRequest, String> {
    let hole_cards = [
        parse_card(&jr.hole_cards[0]).map_err(|e| format!("hole[0]: {e}"))?,
        parse_card(&jr.hole_cards[1]).map_err(|e| format!("hole[1]: {e}"))?,
    ];
    let board: Result<Vec<_>, _> = jr.board.iter()
        .map(|s| parse_card(s).map_err(|e| format!("board card '{s}': {e}")))
        .collect();
    let board = board?;

    let legal_actions: Vec<LiveLegalAction> = jr.legal_actions.iter()
        .map(|la| LiveLegalAction {
            kind:       action_kind_from_str(&la.kind),
            min_amount: la.min,
            max_amount: la.max,
        })
        .collect();

    Ok(RecommendRequest {
        active_seats:       jr.active_seats.clone(),
        button_seat:        jr.button_seat,
        hero_seat:          jr.hero_seat,
        street:             street_from_str(&jr.street),
        effective_stack_bb: jr.effective_stack_bb,
        n_players_in_hand:  jr.n_players_in_hand,
        action_history:     jr.action_history.clone(),
        board_bucket:       jr.board_bucket,
        rake_profile_str:   jr.rake_profile.clone(),
        menu_version:       jr.menu_version,
        hole_cards,
        board,
        facing_bet:         jr.facing_bet,
        pot:                jr.pot,
        big_blind:          jr.big_blind,
        hero_committed:     jr.hero_committed,
        hero_start_stack:   jr.hero_start_stack,
        hero_stack:         jr.hero_stack,
        legal_actions,
        facing_open_amount: jr.facing_open_amount,
        facing_3bet_amount: jr.facing_3bet_amount,
        facing_bet_amount:  jr.facing_bet_amount,
    })
}

// ─── Response conversion ─────────────────────────────────────────────────────

fn convert_response(resp: &RecommendResponse, latency_us: u64) -> JsonResponse {
    let exact = resp.exact.as_ref().map(|e| JsonExact {
        hand_bucket:     hand_bucket_str(e.hand_bucket).into(),
        chosen_kind:     action_kind_str(e.chosen_kind).into(),
        chosen_size:     size_id_str(e.chosen_size).into(),
        probability:     e.probability,
        resolved_amount: e.resolved_amount,
    });

    let emergency = resp.emergency.as_ref().map(|e| JsonEmergency {
        equity:        e.equity,
        hand_bucket:   hand_bucket_str(e.hand_bucket).into(),
        board_texture: board_texture_str(e.board_texture).into(),
        kind:          action_kind_str(e.kind).into(),
        target_amount: e.target_amount,
    });

    JsonResponse {
        mode:          mode_str(resp.mode).into(),
        action_kind:   action_kind_str(resp.action.kind).into(),
        action_amount: resp.action.amount,
        was_snapped:   resp.was_snapped,
        snap_reason:   snap_reason_str(resp.snap_reason.clone()).into(),
        focus_hint:    focus_hint_str(resp.focus_hint).into(),
        quality:       quality_str(resp.quality).into(),
        artifact_key:  artifact_key(&resp.spot_key),
        trust_score:   resp.trust_score,
        latency_us,
        exact,
        emergency,
    }
}

// ─── CLI ─────────────────────────────────────────────────────────────────────

struct Args {
    artifact_root:  PathBuf,
    action_menu:    PathBuf,
    prior_bin:      PathBuf,
    prior_manifest: PathBuf,
    quarantine_dir: PathBuf,
}

fn parse_args() -> Args {
    let args: Vec<String> = std::env::args().collect();
    let mut artifact_root  = None;
    let mut action_menu    = None;
    let mut prior_bin      = None;
    let mut prior_manifest = None;
    let mut quarantine_dir = None;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--artifact-root"  => { artifact_root  = Some(PathBuf::from(&args[i+1])); i += 2; }
            "--action-menu"    => { action_menu    = Some(PathBuf::from(&args[i+1])); i += 2; }
            "--prior-bin"      => { prior_bin      = Some(PathBuf::from(&args[i+1])); i += 2; }
            "--prior-manifest" => { prior_manifest = Some(PathBuf::from(&args[i+1])); i += 2; }
            "--quarantine-dir" => { quarantine_dir = Some(PathBuf::from(&args[i+1])); i += 2; }
            other => { eprintln!("unknown arg: {other}"); std::process::exit(1); }
        }
    }

    Args {
        artifact_root:  artifact_root.expect("--artifact-root required"),
        action_menu:    action_menu.expect("--action-menu required"),
        prior_bin:      prior_bin.expect("--prior-bin required"),
        prior_manifest: prior_manifest.expect("--prior-manifest required"),
        quarantine_dir: quarantine_dir.unwrap_or_else(|| PathBuf::from("quarantine")),
    }
}

fn init_router(args: &Args) -> Router {
    let action_menu = ActionMenuConfig::from_file(args.action_menu.to_str().unwrap())
        .unwrap_or_else(|e| { eprintln!("action menu load failed: {e}"); std::process::exit(1); });

    let prior = EmergencyRangePrior::load(&args.prior_bin, &args.prior_manifest)
        .unwrap_or_else(|e| { eprintln!("prior load failed: {e}"); std::process::exit(1); });

    std::fs::create_dir_all(&args.quarantine_dir).ok();

    let config = RouterConfig {
        artifact_root:             args.artifact_root.clone(),
        quarantine_dir:            args.quarantine_dir.clone(),
        expected_artifact_version: 1,
    };

    Router {
        config,
        prior,
        action_menu,
        trust_config: TrustConfig::default(),
    }
}

fn process_line(router: &Router, line: &str) -> String {
    let start = Instant::now();

    let jr: JsonRequest = match serde_json::from_str(line) {
        Ok(r) => r,
        Err(e) => return serde_json::to_string(&JsonError { error: format!("parse: {e}") }).unwrap(),
    };

    let req = match convert_request(&jr) {
        Ok(r) => r,
        Err(e) => return serde_json::to_string(&JsonError { error: e }).unwrap(),
    };

    let resp = match router.recommend(&req) {
        Ok(r) => r,
        Err(RouteError::Classify(e)) =>
            return serde_json::to_string(&JsonError { error: format!("classify: {e}") }).unwrap(),
    };

    let latency_us = start.elapsed().as_micros() as u64;
    serde_json::to_string(&convert_response(&resp, latency_us)).unwrap()
}

fn main() {
    env_logger::init();

    let args = parse_args();
    let router = init_router(&args);

    eprintln!("advisor-cli ready, reading JSONL from stdin...");

    let stdin  = io::stdin();
    let stdout = io::stdout();
    let mut writer = io::BufWriter::new(stdout.lock());

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(e) => { eprintln!("stdin read error: {e}"); break; }
        };
        if line.trim().is_empty() { continue; }

        let out = process_line(&router, &line);
        writeln!(writer, "{out}").unwrap();
        writer.flush().unwrap();
    }
}
