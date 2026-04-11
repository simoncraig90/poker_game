//! Artifact quarantine: move bad artifacts out of the load path.
//!
//! When `integrity::verify_artifact` fails, the caller must call
//! `quarantine_artifact` before returning an error.  This prevents the same
//! bad file from being retried on every startup and leaves a visible audit
//! trail for post-mortem inspection.

use log::warn;
use std::{fs, path::{Path, PathBuf}};
use thiserror::Error;

// ─── Types ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QuarantineReason {
    ChecksumMismatch,
    VersionMismatch,
    SizeMismatch,
    ManifestMissing,
    ManifestParseError,
    IoError,
    StrategyParseError,
}

impl QuarantineReason {
    pub fn as_str(self) -> &'static str {
        match self {
            QuarantineReason::ChecksumMismatch   => "checksum_mismatch",
            QuarantineReason::VersionMismatch    => "version_mismatch",
            QuarantineReason::SizeMismatch       => "size_mismatch",
            QuarantineReason::ManifestMissing    => "manifest_missing",
            QuarantineReason::ManifestParseError => "manifest_parse_error",
            QuarantineReason::IoError              => "io_error",
            QuarantineReason::StrategyParseError  => "strategy_parse_error",
        }
    }
}

impl From<&crate::IntegrityError> for QuarantineReason {
    fn from(e: &crate::IntegrityError) -> Self {
        match e {
            crate::IntegrityError::ChecksumMismatch { .. } => QuarantineReason::ChecksumMismatch,
            crate::IntegrityError::VersionMismatch  { .. } => QuarantineReason::VersionMismatch,
            crate::IntegrityError::SizeMismatch     { .. } => QuarantineReason::SizeMismatch,
            crate::IntegrityError::ManifestParse    (_)   => QuarantineReason::ManifestParseError,
            crate::IntegrityError::Io               { .. } => QuarantineReason::IoError,
        }
    }
}

#[derive(Debug, Error)]
pub enum QuarantineError {
    #[error("failed to create quarantine directory {dir}: {source}")]
    DirCreate { dir: String, source: std::io::Error },

    #[error("failed to move {src} to quarantine: {source}")]
    MoveFailed { src: String, source: std::io::Error },
}

// ─── Public API ───────────────────────────────────────────────────────────────

/// Move an artifact (and its companion manifest, if present) to `quarantine_dir`.
///
/// The quarantined filename encodes the original name + failure reason so
/// post-mortem inspection is straightforward without reading the file.
///
/// Returns the path the artifact was moved to.
pub fn quarantine_artifact(
    artifact_path: &Path,
    reason:        QuarantineReason,
    quarantine_dir: &Path,
) -> Result<PathBuf, QuarantineError> {
    fs::create_dir_all(quarantine_dir).map_err(|e| QuarantineError::DirCreate {
        dir:    quarantine_dir.display().to_string(),
        source: e,
    })?;

    let stem = artifact_path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "unknown".into());

    let dest_name = format!("{}.{}.quarantined", stem, reason.as_str());
    let dest = quarantine_dir.join(&dest_name);

    warn!(
        "quarantine: {:?} → {:?} ({})",
        artifact_path, dest, reason.as_str()
    );

    fs::rename(artifact_path, &dest).map_err(|e| QuarantineError::MoveFailed {
        src:    artifact_path.display().to_string(),
        source: e,
    })?;

    // Best-effort: also move the companion manifest if it exists.
    // Two conventions: <stem>.manifest.json and manifest.json in same dir.
    let manifest_candidates = [
        artifact_path.with_extension("manifest.json"),
        artifact_path.parent().unwrap_or(Path::new(".")).join("manifest.json"),
    ];
    for m in &manifest_candidates {
        if m.exists() {
            let mname = m.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            let mdest = quarantine_dir.join(format!("{}.{}.quarantined", mname, reason.as_str()));
            let _ = fs::rename(m, mdest); // best-effort, ignore failure
            break;
        }
    }

    Ok(dest)
}

/// Returns true if any quarantined file in `quarantine_dir` was derived from
/// `artifact_path` (i.e., starts with the same filename stem).
pub fn is_quarantined(artifact_path: &Path, quarantine_dir: &Path) -> bool {
    let stem = match artifact_path.file_name() {
        Some(n) => n.to_string_lossy().into_owned(),
        None    => return false,
    };
    let Ok(entries) = fs::read_dir(quarantine_dir) else { return false; };
    entries.flatten().any(|e| e.file_name().to_string_lossy().starts_with(&*stem))
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::{tempdir, NamedTempFile};

    #[test]
    fn quarantine_moves_file() {
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(b"bad artifact").unwrap();
        let orig_path = f.path().to_path_buf();

        let qdir = tempdir().unwrap();
        let dest = quarantine_artifact(&orig_path, QuarantineReason::ChecksumMismatch, qdir.path()).unwrap();

        // File should now be at dest, not at orig_path.
        assert!(dest.exists());
        assert!(!orig_path.exists());
        assert!(dest.to_string_lossy().contains("checksum_mismatch"));
    }

    #[test]
    fn is_quarantined_detects_moved_file() {
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(b"corrupted").unwrap();
        let orig = f.path().to_path_buf();

        let qdir = tempdir().unwrap();
        assert!(!is_quarantined(&orig, qdir.path()));

        quarantine_artifact(&orig, QuarantineReason::VersionMismatch, qdir.path()).unwrap();
        assert!(is_quarantined(&orig, qdir.path()));
    }

    #[test]
    fn is_quarantined_returns_false_for_unknown_dir() {
        let f = NamedTempFile::new().unwrap();
        assert!(!is_quarantined(f.path(), Path::new("/nonexistent/qdir/xyz")));
    }

    #[test]
    fn reason_as_str_covers_all_variants() {
        let reasons = [
            QuarantineReason::ChecksumMismatch,
            QuarantineReason::VersionMismatch,
            QuarantineReason::SizeMismatch,
            QuarantineReason::ManifestMissing,
            QuarantineReason::ManifestParseError,
            QuarantineReason::IoError,
            QuarantineReason::StrategyParseError,
        ];
        for r in reasons {
            assert!(!r.as_str().is_empty());
        }
    }

    #[test]
    fn from_integrity_error_maps_correctly() {
        let e = crate::IntegrityError::ChecksumMismatch {
            expected: "abc".into(), actual: "xyz".into(),
        };
        assert_eq!(QuarantineReason::from(&e), QuarantineReason::ChecksumMismatch);
    }
}
