//! Action vocabulary: kinds, abstract sizes, runtime amounts.
//!
//! The split between `ActionKind` and `AbstractSizeId` keeps the proto stable
//! as coverage grows — adding a new cluster or SPR bucket never needs a new
//! enum variant here.

use serde::Deserialize;
use std::collections::HashMap;

// ─── Enumerations ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ActionKind {
    Fold,
    Check,
    Call,
    BetTo,
    RaiseTo,
    Jam,
}

/// Abstract size identifier — maps 1-to-1 with the proto enum.
/// Numeric amounts are resolved at runtime via `resolve_amount`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AbstractSizeId {
    None,
    // Preflop opens
    OpenStd,
    OpenLarge,
    // Preflop 3-bets
    ThreebetIpStd,
    ThreebetOopStd,
    ThreebetBbWide,
    // Preflop 4-bets
    FourbetStd,
    // Postflop bets
    CbetSmall,
    CbetMedium,
    CbetLarge,
    CbetOverbet,
    // Postflop raises
    RaiseVsSmall,
    RaiseVsLarge,
    // Protection / thin value
    ProtectionValueSm,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Street {
    Preflop,
    Flop,
    Turn,
    River,
}

// ─── Action struct ────────────────────────────────────────────────────────────

/// A resolved action: kind + abstract size label + concrete chip amount.
#[derive(Debug, Clone)]
pub struct Action {
    pub kind: ActionKind,
    pub size_id: AbstractSizeId,
    /// Absolute chip amount.
    /// 0.0 for Fold and Check; hero's total stack for Jam.
    pub amount: f64,
    /// Informational fraction (amount / pot). 0.0 if not applicable.
    pub pot_fraction: f64,
}

// ─── Config (loaded from action_menu_v1.yaml) ─────────────────────────────────

/// Per-position open multipliers (× big blind).
#[derive(Debug, Clone, Deserialize)]
pub struct OpenSizeRow {
    pub utg: f64,
    pub hj:  f64,
    pub co:  f64,
    pub btn: f64,
    pub sb:  f64,
}

/// Full action-menu configuration. Deserialised from YAML.
#[derive(Debug, Clone, Deserialize)]
pub struct ActionMenuConfig {
    pub version:      String,
    pub menu_version: u8,

    pub preflop_opens:             HashMap<String, OpenSizeRow>,
    pub preflop_3bets:             HashMap<String, f64>,
    pub preflop_4bets:             HashMap<String, f64>,
    pub postflop_pot_fractions:    HashMap<String, f64>,
    pub postflop_raise_multipliers: HashMap<String, f64>,
    pub jam_threshold_of_stack:    f64,
}

impl ActionMenuConfig {
    /// Load from a YAML file path.
    pub fn from_file(path: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let text = std::fs::read_to_string(path)?;
        let cfg: Self = serde_yaml::from_str(&text)?;
        cfg.validate()?;
        Ok(cfg)
    }

    pub fn validate(&self) -> Result<(), String> {
        // Every known size ID must be present in config.
        let required_opens = ["OPEN_STD", "OPEN_LARGE"];
        for k in required_opens {
            if !self.preflop_opens.contains_key(k) {
                return Err(format!("action_menu: missing preflop_open key {k}"));
            }
        }
        let required_3bets = ["THREEBET_IP_STD", "THREEBET_OOP_STD", "THREEBET_BB_WIDE"];
        for k in required_3bets {
            if !self.preflop_3bets.contains_key(k) {
                return Err(format!("action_menu: missing preflop_3bet key {k}"));
            }
        }
        if !self.preflop_4bets.contains_key("FOURBET_STD") {
            return Err("action_menu: missing FOURBET_STD".into());
        }
        let required_postflop = ["CBET_SMALL", "CBET_MEDIUM", "CBET_LARGE", "CBET_OVERBET", "PROTECTION_VALUE_SM"];
        for k in required_postflop {
            if !self.postflop_pot_fractions.contains_key(k) {
                return Err(format!("action_menu: missing postflop_pot_fraction key {k}"));
            }
        }
        let required_raises = ["RAISE_VS_SMALL", "RAISE_VS_LARGE"];
        for k in required_raises {
            if !self.postflop_raise_multipliers.contains_key(k) {
                return Err(format!("action_menu: missing postflop_raise_multiplier key {k}"));
            }
        }
        if !(0.5..=1.0).contains(&self.jam_threshold_of_stack) {
            return Err(format!(
                "action_menu: jam_threshold_of_stack {} out of range [0.5, 1.0]",
                self.jam_threshold_of_stack
            ));
        }
        Ok(())
    }
}

// ─── Size context (provided by caller at resolve time) ────────────────────────

/// Everything needed to turn an `AbstractSizeId` into a chip amount.
#[derive(Debug, Clone)]
pub struct SizeContext {
    pub pot: f64,
    pub big_blind: f64,
    /// Hero position string: "utg" | "hj" | "co" | "btn" | "sb" | "bb"
    pub hero_position: String,
    /// Amount of the open we're 3-betting over (preflop).
    pub facing_open_amount: f64,
    /// Amount of the 3-bet we're 4-betting over (preflop).
    pub facing_3bet_amount: f64,
    /// Amount of the villain's postflop bet we're raising over.
    pub facing_bet_amount: f64,
    /// Hero's current stack (used for jam threshold check).
    pub hero_stack: f64,
}

// ─── Amount resolution ────────────────────────────────────────────────────────

/// Compute the concrete chip amount for a given abstract size.
///
/// Returns `None` if:
/// - `size_id` is `None` (caller should use FOLD/CHECK/CALL/JAM directly)
/// - Required context value is zero/missing (e.g. open-raise into a 3-bet size
///   when `facing_open_amount` is 0)
/// - Position is unrecognised for an open-size lookup
pub fn resolve_amount(
    size_id: AbstractSizeId,
    ctx: &SizeContext,
    cfg: &ActionMenuConfig,
) -> Option<f64> {
    let pos = ctx.hero_position.to_ascii_lowercase();

    match size_id {
        AbstractSizeId::None => None,

        // ── Preflop opens ────────────────────────────────────────────────────
        AbstractSizeId::OpenStd | AbstractSizeId::OpenLarge => {
            let key = if size_id == AbstractSizeId::OpenStd { "OPEN_STD" } else { "OPEN_LARGE" };
            let row = cfg.preflop_opens.get(key)?;
            let mult = position_multiplier(row, &pos)?;
            Some(mult * ctx.big_blind)
        }

        // ── Preflop 3-bets ───────────────────────────────────────────────────
        AbstractSizeId::ThreebetIpStd => {
            let mult = cfg.preflop_3bets.get("THREEBET_IP_STD")?;
            if ctx.facing_open_amount <= 0.0 { return None; }
            Some(mult * ctx.facing_open_amount)
        }
        AbstractSizeId::ThreebetOopStd => {
            let mult = cfg.preflop_3bets.get("THREEBET_OOP_STD")?;
            if ctx.facing_open_amount <= 0.0 { return None; }
            Some(mult * ctx.facing_open_amount)
        }
        AbstractSizeId::ThreebetBbWide => {
            let mult = cfg.preflop_3bets.get("THREEBET_BB_WIDE")?;
            if ctx.facing_open_amount <= 0.0 { return None; }
            Some(mult * ctx.facing_open_amount)
        }

        // ── Preflop 4-bets ───────────────────────────────────────────────────
        AbstractSizeId::FourbetStd => {
            let mult = cfg.preflop_4bets.get("FOURBET_STD")?;
            if ctx.facing_3bet_amount <= 0.0 { return None; }
            Some(mult * ctx.facing_3bet_amount)
        }

        // ── Postflop bets ────────────────────────────────────────────────────
        AbstractSizeId::CbetSmall => {
            let frac = cfg.postflop_pot_fractions.get("CBET_SMALL")?;
            Some(frac * ctx.pot)
        }
        AbstractSizeId::CbetMedium => {
            let frac = cfg.postflop_pot_fractions.get("CBET_MEDIUM")?;
            Some(frac * ctx.pot)
        }
        AbstractSizeId::CbetLarge => {
            let frac = cfg.postflop_pot_fractions.get("CBET_LARGE")?;
            Some(frac * ctx.pot)
        }
        AbstractSizeId::CbetOverbet => {
            let frac = cfg.postflop_pot_fractions.get("CBET_OVERBET")?;
            Some(frac * ctx.pot)
        }
        AbstractSizeId::ProtectionValueSm => {
            let frac = cfg.postflop_pot_fractions.get("PROTECTION_VALUE_SM")?;
            Some(frac * ctx.pot)
        }

        // ── Postflop raises ──────────────────────────────────────────────────
        AbstractSizeId::RaiseVsSmall => {
            let mult = cfg.postflop_raise_multipliers.get("RAISE_VS_SMALL")?;
            if ctx.facing_bet_amount <= 0.0 { return None; }
            Some(mult * ctx.facing_bet_amount)
        }
        AbstractSizeId::RaiseVsLarge => {
            let mult = cfg.postflop_raise_multipliers.get("RAISE_VS_LARGE")?;
            if ctx.facing_bet_amount <= 0.0 { return None; }
            Some(mult * ctx.facing_bet_amount)
        }
    }
}

fn position_multiplier(row: &OpenSizeRow, pos: &str) -> Option<f64> {
    match pos {
        "utg" => Some(row.utg),
        "hj"  => Some(row.hj),
        "co"  => Some(row.co),
        "btn" => Some(row.btn),
        "sb"  => Some(row.sb),
        _     => None,
    }
}

// ─── Wire serialization (binary artifact format) ─────────────────────────────

impl ActionKind {
    pub fn to_wire_u8(self) -> u8 {
        match self {
            ActionKind::Fold    => 0,
            ActionKind::Check   => 1,
            ActionKind::Call    => 2,
            ActionKind::BetTo   => 3,
            ActionKind::RaiseTo => 4,
            ActionKind::Jam     => 5,
        }
    }

    pub fn from_wire_u8(v: u8) -> Option<Self> {
        match v {
            0 => Some(ActionKind::Fold),
            1 => Some(ActionKind::Check),
            2 => Some(ActionKind::Call),
            3 => Some(ActionKind::BetTo),
            4 => Some(ActionKind::RaiseTo),
            5 => Some(ActionKind::Jam),
            _ => None,
        }
    }
}

impl AbstractSizeId {
    pub fn to_wire_u8(self) -> u8 {
        match self {
            AbstractSizeId::None              => 0,
            AbstractSizeId::OpenStd           => 1,
            AbstractSizeId::OpenLarge         => 2,
            AbstractSizeId::ThreebetIpStd     => 3,
            AbstractSizeId::ThreebetOopStd    => 4,
            AbstractSizeId::ThreebetBbWide    => 5,
            AbstractSizeId::FourbetStd        => 6,
            AbstractSizeId::CbetSmall         => 7,
            AbstractSizeId::CbetMedium        => 8,
            AbstractSizeId::CbetLarge         => 9,
            AbstractSizeId::CbetOverbet       => 10,
            AbstractSizeId::RaiseVsSmall      => 11,
            AbstractSizeId::RaiseVsLarge      => 12,
            AbstractSizeId::ProtectionValueSm => 13,
        }
    }

    pub fn from_wire_u8(v: u8) -> Option<Self> {
        match v {
            0  => Some(AbstractSizeId::None),
            1  => Some(AbstractSizeId::OpenStd),
            2  => Some(AbstractSizeId::OpenLarge),
            3  => Some(AbstractSizeId::ThreebetIpStd),
            4  => Some(AbstractSizeId::ThreebetOopStd),
            5  => Some(AbstractSizeId::ThreebetBbWide),
            6  => Some(AbstractSizeId::FourbetStd),
            7  => Some(AbstractSizeId::CbetSmall),
            8  => Some(AbstractSizeId::CbetMedium),
            9  => Some(AbstractSizeId::CbetLarge),
            10 => Some(AbstractSizeId::CbetOverbet),
            11 => Some(AbstractSizeId::RaiseVsSmall),
            12 => Some(AbstractSizeId::RaiseVsLarge),
            13 => Some(AbstractSizeId::ProtectionValueSm),
            _  => None,
        }
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn test_cfg() -> ActionMenuConfig {
        let yaml = r#"
version: "v1"
menu_version: 1
preflop_opens:
  OPEN_STD:
    utg: 2.2
    hj:  2.2
    co:  2.2
    btn: 2.5
    sb:  3.0
  OPEN_LARGE:
    utg: 3.0
    hj:  3.0
    co:  3.0
    btn: 3.0
    sb:  4.0
preflop_3bets:
  THREEBET_IP_STD:  3.2
  THREEBET_OOP_STD: 4.0
  THREEBET_BB_WIDE: 4.5
preflop_4bets:
  FOURBET_STD: 2.3
postflop_pot_fractions:
  CBET_SMALL:          0.33
  CBET_MEDIUM:         0.60
  CBET_LARGE:          0.80
  CBET_OVERBET:        1.25
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

    fn ctx(pot: f64, bb: f64, pos: &str) -> SizeContext {
        SizeContext {
            pot,
            big_blind: bb,
            hero_position: pos.to_string(),
            facing_open_amount: 0.0,
            facing_3bet_amount: 0.0,
            facing_bet_amount: 0.0,
            hero_stack: 1000.0,
        }
    }

    #[test]
    fn open_std_btn() {
        let cfg = test_cfg();
        let c = ctx(0.0, 100.0, "btn");
        let amt = resolve_amount(AbstractSizeId::OpenStd, &c, &cfg).unwrap();
        assert!((amt - 250.0).abs() < 0.01, "BTN open std should be 2.5×100=250, got {amt}");
    }

    #[test]
    fn open_std_utg() {
        let cfg = test_cfg();
        let c = ctx(0.0, 100.0, "utg");
        let amt = resolve_amount(AbstractSizeId::OpenStd, &c, &cfg).unwrap();
        assert!((amt - 220.0).abs() < 0.01);
    }

    #[test]
    fn open_std_sb() {
        let cfg = test_cfg();
        let c = ctx(0.0, 100.0, "sb");
        let amt = resolve_amount(AbstractSizeId::OpenStd, &c, &cfg).unwrap();
        assert!((amt - 300.0).abs() < 0.01);
    }

    #[test]
    fn threebet_oop_requires_facing_open() {
        let cfg = test_cfg();
        let c = ctx(0.0, 100.0, "bb"); // facing_open_amount = 0
        assert!(resolve_amount(AbstractSizeId::ThreebetOopStd, &c, &cfg).is_none());
    }

    #[test]
    fn threebet_oop_with_open() {
        let cfg = test_cfg();
        let mut c = ctx(0.0, 100.0, "bb");
        c.facing_open_amount = 250.0; // BTN open
        let amt = resolve_amount(AbstractSizeId::ThreebetOopStd, &c, &cfg).unwrap();
        assert!((amt - 1000.0).abs() < 0.01, "OOP 3bet = 4.0×250=1000, got {amt}");
    }

    #[test]
    fn cbet_medium_pot() {
        let cfg = test_cfg();
        let c = ctx(600.0, 100.0, "btn");
        let amt = resolve_amount(AbstractSizeId::CbetMedium, &c, &cfg).unwrap();
        assert!((amt - 360.0).abs() < 0.01, "CBET_MEDIUM = 0.6×600=360, got {amt}");
    }

    #[test]
    fn raise_vs_small_requires_facing_bet() {
        let cfg = test_cfg();
        let c = ctx(300.0, 100.0, "btn");
        assert!(resolve_amount(AbstractSizeId::RaiseVsSmall, &c, &cfg).is_none());
    }

    #[test]
    fn raise_vs_small_with_bet() {
        let cfg = test_cfg();
        let mut c = ctx(300.0, 100.0, "btn");
        c.facing_bet_amount = 100.0;
        let amt = resolve_amount(AbstractSizeId::RaiseVsSmall, &c, &cfg).unwrap();
        assert!((amt - 300.0).abs() < 0.01, "RAISE_VS_SMALL = 3.0×100=300, got {amt}");
    }

    #[test]
    fn size_none_returns_none() {
        let cfg = test_cfg();
        let c = ctx(300.0, 100.0, "btn");
        assert!(resolve_amount(AbstractSizeId::None, &c, &cfg).is_none());
    }

    #[test]
    fn unknown_position_returns_none() {
        let cfg = test_cfg();
        let c = ctx(0.0, 100.0, "dealer"); // not a valid position key
        assert!(resolve_amount(AbstractSizeId::OpenStd, &c, &cfg).is_none());
    }

    #[test]
    fn validate_passes_on_good_config() {
        test_cfg().validate().unwrap();
    }

    // ── Wire serialization round-trips ───────────────────────────────────────

    #[test]
    fn action_kind_wire_round_trip() {
        let all = [
            ActionKind::Fold, ActionKind::Check, ActionKind::Call,
            ActionKind::BetTo, ActionKind::RaiseTo, ActionKind::Jam,
        ];
        for k in all {
            let wire = k.to_wire_u8();
            assert_eq!(ActionKind::from_wire_u8(wire), Some(k), "round-trip failed for {:?}", k);
        }
    }

    #[test]
    fn action_kind_from_wire_invalid() {
        assert!(ActionKind::from_wire_u8(6).is_none());
        assert!(ActionKind::from_wire_u8(255).is_none());
    }

    #[test]
    fn abstract_size_wire_round_trip() {
        let all = [
            AbstractSizeId::None, AbstractSizeId::OpenStd, AbstractSizeId::OpenLarge,
            AbstractSizeId::ThreebetIpStd, AbstractSizeId::ThreebetOopStd,
            AbstractSizeId::ThreebetBbWide, AbstractSizeId::FourbetStd,
            AbstractSizeId::CbetSmall, AbstractSizeId::CbetMedium,
            AbstractSizeId::CbetLarge, AbstractSizeId::CbetOverbet,
            AbstractSizeId::RaiseVsSmall, AbstractSizeId::RaiseVsLarge,
            AbstractSizeId::ProtectionValueSm,
        ];
        for s in all {
            let wire = s.to_wire_u8();
            assert_eq!(AbstractSizeId::from_wire_u8(wire), Some(s), "round-trip failed for {:?}", s);
        }
    }

    #[test]
    fn abstract_size_from_wire_invalid() {
        assert!(AbstractSizeId::from_wire_u8(14).is_none());
        assert!(AbstractSizeId::from_wire_u8(255).is_none());
    }
}
