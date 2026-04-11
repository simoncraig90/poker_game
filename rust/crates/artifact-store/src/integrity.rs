//! Artifact integrity: SHA-256 checksum verification and manifest version enforcement.
//!
//! Every artifact that enters the runtime path must pass `verify_artifact` before use.
//! A failed check must trigger quarantine — never silently degrade.

use hex;
use log::error;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs, io, path::Path};
use thiserror::Error;

// ─── Error type ───────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum IntegrityError {
    #[error("I/O error at {path}: {source}")]
    Io {
        path:   String,
        #[source]
        source: io::Error,
    },

    #[error("checksum mismatch — expected {expected}, got {actual}")]
    ChecksumMismatch { expected: String, actual: String },

    #[error("manifest version mismatch — expected {expected}, got {actual}")]
    VersionMismatch { expected: u32, actual: u32 },

    #[error("file size mismatch — expected {expected} bytes, got {actual} bytes")]
    SizeMismatch { expected: u64, actual: u64 },

    #[error("manifest parse error: {0}")]
    ManifestParse(String),
}

// ─── Manifest ─────────────────────────────────────────────────────────────────

/// JSON manifest that lives alongside every artifact file.
/// `artifact_type` distinguishes solver artifacts from the emergency prior.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactManifest {
    pub artifact_type:   String,
    pub version:         u32,
    pub checksum_sha256: String,
    pub file_size_bytes: u64,

    // ── Solver artifact fields (absent on emergency prior) ─────────────────
    #[serde(skip_serializing_if = "Option::is_none")]
    pub menu_version: Option<u8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exploitability_pct: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scenario_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub n_actions: Option<usize>,

    // ── Emergency prior fields (absent on solver artifacts) ────────────────
    #[serde(skip_serializing_if = "Option::is_none")]
    pub n_hand_buckets: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub n_board_textures: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub n_pot_classes: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub n_aggressor_roles: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub n_player_buckets: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub index_order: Option<String>,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
}

// ─── Public API ───────────────────────────────────────────────────────────────

/// Parse a manifest JSON file from disk.
pub fn load_manifest(path: &Path) -> Result<ArtifactManifest, IntegrityError> {
    let text = fs::read_to_string(path).map_err(|e| IntegrityError::Io {
        path:   path.display().to_string(),
        source: e,
    })?;
    serde_json::from_str(&text).map_err(|e| IntegrityError::ManifestParse(e.to_string()))
}

/// Compute the SHA-256 hex digest of a byte slice (used when data is already in memory).
pub fn checksum_bytes(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

/// Compute the SHA-256 hex digest of a file on disk.
pub fn checksum_file(path: &Path) -> Result<String, IntegrityError> {
    let bytes = fs::read(path).map_err(|e| IntegrityError::Io {
        path:   path.display().to_string(),
        source: e,
    })?;
    Ok(checksum_bytes(&bytes))
}

/// Verify an artifact file against its manifest.
///
/// Checks, in order:
/// 1. Manifest version matches `expected_version`.
/// 2. File size matches manifest.
/// 3. SHA-256 checksum matches manifest.
///
/// All failures are logged at ERROR level before being returned.
pub fn verify_artifact(
    artifact_path:    &Path,
    manifest:         &ArtifactManifest,
    expected_version: u32,
) -> Result<(), IntegrityError> {
    // 1. Version
    if manifest.version != expected_version {
        let err = IntegrityError::VersionMismatch {
            expected: expected_version,
            actual:   manifest.version,
        };
        error!("integrity: {} — {}", artifact_path.display(), err);
        return Err(err);
    }

    // 2. File size (cheap check before hashing)
    let meta = fs::metadata(artifact_path).map_err(|e| IntegrityError::Io {
        path:   artifact_path.display().to_string(),
        source: e,
    })?;
    if meta.len() != manifest.file_size_bytes {
        let err = IntegrityError::SizeMismatch {
            expected: manifest.file_size_bytes,
            actual:   meta.len(),
        };
        error!("integrity: {} — {}", artifact_path.display(), err);
        return Err(err);
    }

    // 3. Checksum
    let actual = checksum_file(artifact_path)?;
    if actual != manifest.checksum_sha256 {
        let err = IntegrityError::ChecksumMismatch {
            expected: manifest.checksum_sha256.clone(),
            actual,
        };
        error!("integrity: {} — {}", artifact_path.display(), err);
        return Err(err);
    }

    Ok(())
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn write_temp(data: &[u8]) -> NamedTempFile {
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(data).unwrap();
        f
    }

    fn manifest_for(data: &[u8], version: u32) -> ArtifactManifest {
        ArtifactManifest {
            artifact_type:     "test".into(),
            version,
            checksum_sha256:   checksum_bytes(data),
            file_size_bytes:   data.len() as u64,
            menu_version:      None,
            exploitability_pct: None,
            scenario_id:       None,
            n_actions:         None,
            n_hand_buckets:    None,
            n_board_textures:  None,
            n_pot_classes:     None,
            n_aggressor_roles: None,
            n_player_buckets:  None,
            index_order:       None,
            created_at:        None,
        }
    }

    #[test]
    fn valid_artifact_passes() {
        let data = b"hello solver";
        let f = write_temp(data);
        let m = manifest_for(data, 1);
        verify_artifact(f.path(), &m, 1).unwrap();
    }

    #[test]
    fn bad_checksum_fails() {
        let data = b"good data";
        let f = write_temp(data);
        let mut m = manifest_for(data, 1);
        m.checksum_sha256 = "deadbeef".repeat(8); // wrong hash
        let err = verify_artifact(f.path(), &m, 1).unwrap_err();
        assert!(matches!(err, IntegrityError::ChecksumMismatch { .. }));
    }

    #[test]
    fn version_mismatch_fails() {
        let data = b"artifact bytes";
        let f = write_temp(data);
        let m = manifest_for(data, 2); // manifest says v2
        let err = verify_artifact(f.path(), &m, 1).unwrap_err(); // expected v1
        assert!(matches!(err, IntegrityError::VersionMismatch { expected: 1, actual: 2 }));
    }

    #[test]
    fn size_mismatch_fails() {
        let data = b"size test";
        let f = write_temp(data);
        let mut m = manifest_for(data, 1);
        m.file_size_bytes = 999; // wrong size
        let err = verify_artifact(f.path(), &m, 1).unwrap_err();
        assert!(matches!(err, IntegrityError::SizeMismatch { .. }));
    }

    #[test]
    fn checksum_bytes_is_deterministic() {
        let h1 = checksum_bytes(b"poker");
        let h2 = checksum_bytes(b"poker");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 64); // 32 bytes hex-encoded
    }

    #[test]
    fn checksum_bytes_different_data() {
        assert_ne!(checksum_bytes(b"AAA"), checksum_bytes(b"KKK"));
    }

    #[test]
    fn manifest_round_trips_json() {
        let m = manifest_for(b"round trip test", 1);
        let json = serde_json::to_string(&m).unwrap();
        let m2: ArtifactManifest = serde_json::from_str(&json).unwrap();
        assert_eq!(m2.version, 1);
        assert_eq!(m2.checksum_sha256, m.checksum_sha256);
    }

    #[test]
    fn load_manifest_from_file() {
        let m = manifest_for(b"test", 1);
        let json = serde_json::to_string_pretty(&m).unwrap();
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(json.as_bytes()).unwrap();
        let loaded = load_manifest(f.path()).unwrap();
        assert_eq!(loaded.version, 1);
        assert_eq!(loaded.artifact_type, "test");
    }

    #[test]
    fn load_manifest_bad_json_fails() {
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(b"not json {{").unwrap();
        assert!(load_manifest(f.path()).is_err());
    }
}
