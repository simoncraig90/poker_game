//! Deterministic artifact key derivation from a SpotKey.
//!
//! Format:
//!   {pot_class}/{street}/{agg}_vs_{hero}_{n}way/{stack}/{board}/{rake}/mv{version}
//!
//! Examples:
//!   srp/flop/btn_vs_bb_2way/s100/bb42/norake/mv1
//!   3bp/turn/sb_vs_btn_2way/s100/bb10/norake/mv1
//!   limped/flop/noagg_vs_bb_3way/s100/bb7/norake/mv1
//!
//! Rules:
//!   - pot_class: "srp", "3bp", "4bp", "squeeze", "limped"
//!   - street:    "preflop", "flop", "turn", "river"
//!   - aggressor: position string or "noagg" for limped
//!   - board:     "bb{cluster_idx}" for postflop, "preflop" for preflop
//!   - rake:      RakeProfile::as_str()
//!   - version:   menu_version as decimal

use crate::classify::{PotClass, SpotKey};
use crate::action::Street;

/// Produce the deterministic artifact key string for a SpotKey.
pub fn artifact_key(key: &SpotKey) -> String {
    let pot = pot_class_str(key.pot_class);
    let street = street_str(key.street);
    let agg = key.aggressor_pos.map(|p| p.as_str()).unwrap_or("noagg");
    let hero = key.hero_pos.as_str();
    let nway = key.n_players;
    let stack = key.stack_bucket.as_str();
    let board = board_str(key.street, key.board_bucket);
    let rake = key.rake_profile.as_str();
    let ver = key.menu_version;

    format!("{pot}/{street}/{agg}_vs_{hero}_{nway}way/{stack}/{board}/{rake}/mv{ver}")
}

fn pot_class_str(pc: PotClass) -> &'static str {
    match pc {
        PotClass::Limped  => "limped",
        PotClass::Srp     => "srp",
        PotClass::ThreeBp => "3bp",
        PotClass::FourBp  => "4bp",
        PotClass::Squeeze => "squeeze",
    }
}

fn street_str(s: Street) -> &'static str {
    match s {
        Street::Preflop => "preflop",
        Street::Flop    => "flop",
        Street::Turn    => "turn",
        Street::River   => "river",
    }
}

fn board_str(street: Street, board_bucket: Option<u8>) -> String {
    match street {
        Street::Preflop => "preflop".to_string(),
        _ => match board_bucket {
            Some(b) => format!("bb{b}"),
            None    => "bbunk".to_string(), // should not occur for postflop spots
        },
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::classify::{Position, RakeProfile, StackBucket};
    use crate::action::Street;

    fn base_key() -> SpotKey {
        SpotKey {
            pot_class:          PotClass::Srp,
            street:             Street::Flop,
            aggressor_pos:      Some(Position::Btn),
            hero_pos:           Position::Bb,
            n_players:          2,
            stack_bucket:       StackBucket::S100,
            effective_stack_bb: 97.5,
            board_bucket:       Some(42),
            rake_profile:       RakeProfile::NoRake,
            menu_version:       1,
        }
    }

    #[test]
    fn canonical_srp_btn_vs_bb() {
        let k = base_key();
        assert_eq!(artifact_key(&k), "srp/flop/btn_vs_bb_2way/s100/bb42/norake/mv1");
    }

    #[test]
    fn three_bet_pot_turn() {
        let mut k = base_key();
        k.pot_class     = PotClass::ThreeBp;
        k.street        = Street::Turn;
        k.aggressor_pos = Some(Position::Sb);
        k.hero_pos      = Position::Btn;
        k.board_bucket  = Some(10);
        assert_eq!(artifact_key(&k), "3bp/turn/sb_vs_btn_2way/s100/bb10/norake/mv1");
    }

    #[test]
    fn limped_no_aggressor() {
        let mut k = base_key();
        k.pot_class     = PotClass::Limped;
        k.aggressor_pos = None;
        k.hero_pos      = Position::Bb;
        k.n_players     = 3;
        k.board_bucket  = Some(7);
        assert_eq!(artifact_key(&k), "limped/flop/noagg_vs_bb_3way/s100/bb7/norake/mv1");
    }

    #[test]
    fn preflop_board_segment() {
        let mut k = base_key();
        k.street       = Street::Preflop;
        k.board_bucket = None;
        assert_eq!(artifact_key(&k), "srp/preflop/btn_vs_bb_2way/s100/preflop/norake/mv1");
    }

    #[test]
    fn squeeze_pot_river() {
        let mut k = base_key();
        k.pot_class    = PotClass::Squeeze;
        k.street       = Street::River;
        k.board_bucket = Some(0);
        assert_eq!(artifact_key(&k), "squeeze/river/btn_vs_bb_2way/s100/bb0/norake/mv1");
    }

    #[test]
    fn rake_profile_in_key() {
        let mut k = base_key();
        k.rake_profile = RakeProfile::Rake5Cap1Bb;
        assert_eq!(artifact_key(&k), "srp/flop/btn_vs_bb_2way/s100/bb42/rake5cap1bb/mv1");
    }

    #[test]
    fn menu_version_in_key() {
        let mut k = base_key();
        k.menu_version = 2;
        assert_eq!(artifact_key(&k), "srp/flop/btn_vs_bb_2way/s100/bb42/norake/mv2");
    }

    #[test]
    fn s40_stack_bucket() {
        let mut k = base_key();
        k.stack_bucket = StackBucket::S40;
        assert_eq!(artifact_key(&k), "srp/flop/btn_vs_bb_2way/s40/bb42/norake/mv1");
    }

    #[test]
    fn four_bet_pot() {
        let mut k = base_key();
        k.pot_class     = PotClass::FourBp;
        k.aggressor_pos = Some(Position::Co);
        k.hero_pos      = Position::Btn;
        assert_eq!(artifact_key(&k), "4bp/flop/co_vs_btn_2way/s100/bb42/norake/mv1");
    }

    #[test]
    fn nway_six_handed() {
        let mut k = base_key();
        k.n_players = 6;
        assert_eq!(artifact_key(&k), "srp/flop/btn_vs_bb_6way/s100/bb42/norake/mv1");
    }

    #[test]
    fn unknown_board_bucket_uses_bbunk() {
        let mut k = base_key();
        k.board_bucket = None;
        k.street       = Street::Flop; // postflop without bucket
        assert_eq!(artifact_key(&k), "srp/flop/btn_vs_bb_2way/s100/bbunk/norake/mv1");
    }
}
