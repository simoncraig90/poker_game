"use strict";

/**
 * Bot Detection Analyzer
 *
 * Scores each player action on multiple signals. Maintains a rolling
 * profile per seat and flags suspicious patterns.
 *
 * Detection signals:
 *   1. Reaction time consistency (bots have low variance)
 *   2. Mouse trail absence or mechanical paths (straight lines)
 *   3. Click precision (bots hit exact center every time)
 *   4. Keystroke regularity (bots type at constant speed)
 *   5. Missing telemetry (WebSocket-only bots send no mouse data)
 *   6. Action timing patterns (inhuman speed or perfect consistency)
 *   7. Idle cursor between actions (no casual mouse movement)
 */

// ── Thresholds ────────────────────────────────────────────────────────────

const THRESHOLDS = {
  // Reaction time
  MIN_HUMAN_REACTION_MS: 300,       // faster than this is suspicious
  REACTION_VARIANCE_MIN: 800,       // humans vary at least this much (ms² variance)

  // Mouse trail
  MIN_TRAIL_LENGTH: 5,              // humans move the mouse at least a little
  STRAIGHT_PATH_THRESHOLD: 0.95,    // straightness > this = mechanical
  SPEED_VARIANCE_MIN: 0.02,         // humans have variable mouse speed

  // Click precision
  MAX_CENTER_HITS: 0.8,             // if >80% of clicks are within 3px of center

  // Keystroke
  KEYSTROKE_VARIANCE_MIN: 100,      // humans vary keystroke timing (ms²)

  // Action count for analysis
  MIN_ACTIONS_FOR_SCORING: 5,       // need at least N actions before flagging

  // Score thresholds
  WARN_SCORE: 0.40,                 // yellow flag
  FLAG_SCORE: 0.55,                 // red flag — likely bot
};

// ── Per-seat profile ──────────────────────────────────────────────────────

class PlayerProfile {
  constructor(seat) {
    this.seat = seat;
    this.actions = [];               // rolling window of action telemetry
    this.scores = [];                // per-action bot scores
    this.maxActions = 100;           // keep last N actions
    this.flagCount = 0;
    this.warnCount = 0;
    this.currentScore = 0;
    this.signals = {};               // latest signal breakdown
  }

  addAction(telemetry) {
    this.actions.push(telemetry);
    if (this.actions.length > this.maxActions) this.actions.shift();
  }
}

// ── Main Analyzer ─────────────────────────────────────────────────────────

class BotDetector {
  constructor(options = {}) {
    this.profiles = {};              // seat -> PlayerProfile
    this.thresholds = { ...THRESHOLDS, ...options.thresholds };
    this.onWarn = options.onWarn || null;   // callback(seat, score, signals)
    this.onFlag = options.onFlag || null;   // callback(seat, score, signals)
    this.enabled = options.enabled !== false;
  }

  /**
   * Analyze a player action with its telemetry data.
   * @param {number} seat - seat index
   * @param {object} telemetry - client telemetry snapshot (or null)
   * @returns {{ score: number, signals: object, level: string }}
   */
  analyze(seat, telemetry) {
    if (!this.enabled) return { score: 0, signals: {}, level: "ok" };

    if (!this.profiles[seat]) {
      this.profiles[seat] = new PlayerProfile(seat);
    }
    const profile = this.profiles[seat];
    profile.addAction(telemetry || {});

    // Not enough data yet
    if (profile.actions.length < this.thresholds.MIN_ACTIONS_FOR_SCORING) {
      return { score: 0, signals: {}, level: "insufficient_data" };
    }

    // Run all signal detectors
    const signals = {};
    signals.missingTelemetry = this._scoreMissingTelemetry(profile);
    signals.reactionTime = this._scoreReactionTime(profile);
    signals.reactionConsistency = this._scoreReactionConsistency(profile);
    signals.mouseAbsence = this._scoreMouseAbsence(profile);
    signals.mouseStraightness = this._scoreMouseStraightness(profile);
    signals.mouseSpeedConsistency = this._scoreMouseSpeedConsistency(profile);
    signals.clickPrecision = this._scoreClickPrecision(profile);
    signals.keystrokeRegularity = this._scoreKeystrokeRegularity(profile);
    signals.idleStreak = this._scoreIdleStreak(profile);

    // Weighted composite score
    const weights = {
      missingTelemetry: 0.30,
      reactionTime: 0.08,
      reactionConsistency: 0.12,
      mouseAbsence: 0.20,
      mouseStraightness: 0.07,
      mouseSpeedConsistency: 0.06,
      clickPrecision: 0.07,
      keystrokeRegularity: 0.04,
      idleStreak: 0.06,
    };

    let score = 0;
    for (const [key, weight] of Object.entries(weights)) {
      score += (signals[key] || 0) * weight;
    }
    score = Math.min(1, Math.max(0, score));

    profile.currentScore = score;
    profile.signals = signals;
    profile.scores.push(score);
    if (profile.scores.length > this.thresholds.MIN_ACTIONS_FOR_SCORING * 2) {
      profile.scores.shift();
    }

    // Determine level
    let level = "ok";
    if (score >= this.thresholds.FLAG_SCORE) {
      level = "flag";
      profile.flagCount++;
      if (this.onFlag) this.onFlag(seat, score, signals);
    } else if (score >= this.thresholds.WARN_SCORE) {
      level = "warn";
      profile.warnCount++;
      if (this.onWarn) this.onWarn(seat, score, signals);
    }

    return { score, signals, level };
  }

  /**
   * Get the current profile summary for a seat.
   */
  getProfile(seat) {
    const p = this.profiles[seat];
    if (!p) return null;
    return {
      seat: p.seat,
      actionCount: p.actions.length,
      currentScore: p.currentScore,
      avgScore: p.scores.length > 0
        ? p.scores.reduce((a, b) => a + b, 0) / p.scores.length
        : 0,
      flagCount: p.flagCount,
      warnCount: p.warnCount,
      signals: p.signals,
    };
  }

  /**
   * Get all profiles.
   */
  getAllProfiles() {
    return Object.keys(this.profiles).map(s => this.getProfile(parseInt(s)));
  }

  /**
   * Reset profile for a seat (e.g., when player leaves).
   */
  resetSeat(seat) {
    delete this.profiles[seat];
  }

  // ── Signal Scorers (each returns 0-1, higher = more bot-like) ───────

  _scoreMissingTelemetry(profile) {
    // If telemetry is consistently null/empty, it's a WS-only bot
    const recent = profile.actions.slice(-10);
    const missing = recent.filter(t => !t || t.reactionMs == null).length;
    return missing / recent.length;
  }

  _scoreReactionTime(profile) {
    // Inhuman reaction speed
    const recent = profile.actions.slice(-10);
    const reactions = recent.map(t => t.reactionMs).filter(r => r > 0);
    if (reactions.length < 3) return 0;

    const tooFast = reactions.filter(r => r < this.thresholds.MIN_HUMAN_REACTION_MS).length;
    return tooFast / reactions.length;
  }

  _scoreReactionConsistency(profile) {
    // Bots have very consistent reaction times; humans vary
    const recent = profile.actions.slice(-15);
    const reactions = recent.map(t => t.reactionMs).filter(r => r > 0);
    if (reactions.length < 5) return 0;

    const v = variance(reactions);
    if (v < this.thresholds.REACTION_VARIANCE_MIN) {
      // Very consistent — suspicious
      return Math.min(1, 1 - (v / this.thresholds.REACTION_VARIANCE_MIN));
    }
    return 0;
  }

  _scoreMouseAbsence(profile) {
    // No mouse movement at all between actions (or no telemetry = no mouse)
    const recent = profile.actions.slice(-10);
    const noMouse = recent.filter(t => !t || t.trailLength == null || t.trailLength < this.thresholds.MIN_TRAIL_LENGTH).length;
    return noMouse / recent.length;
  }

  _scoreMouseStraightness(profile) {
    // Mechanical straight-line mouse paths
    const recent = profile.actions.slice(-10);
    const straight = recent
      .filter(t => t.trailStraightness != null && t.trailLength > 10)
      .filter(t => t.trailStraightness > this.thresholds.STRAIGHT_PATH_THRESHOLD);
    const eligible = recent.filter(t => t.trailLength > 10).length;
    if (eligible < 3) return 0;
    return straight.length / eligible;
  }

  _scoreMouseSpeedConsistency(profile) {
    // Bots (even with jitter) have very consistent movement speed profiles
    const recent = profile.actions.slice(-10);
    const variances = recent.map(t => t.trailSpeedVariance).filter(v => v != null && v >= 0);
    if (variances.length < 3) return 0;

    const lowVariance = variances.filter(v => v < this.thresholds.SPEED_VARIANCE_MIN).length;
    return lowVariance / variances.length;
  }

  _scoreClickPrecision(profile) {
    // Humans don't hit the exact center of buttons every time
    const recent = profile.actions.slice(-15);
    const dists = recent.map(t => t.clickDistFromCenter).filter(d => d != null);
    if (dists.length < 5) return 0;

    const centerHits = dists.filter(d => d < 3).length; // within 3px of center
    const ratio = centerHits / dists.length;
    return ratio > this.thresholds.MAX_CENTER_HITS ? ratio : 0;
  }

  _scoreKeystrokeRegularity(profile) {
    // Bot keystroke timing is too regular
    const recent = profile.actions.slice(-10);
    const variances = recent.map(t => t.keystrokeVariance).filter(v => v != null);
    if (variances.length < 2) return 0;

    const tooRegular = variances.filter(v => v < this.thresholds.KEYSTROKE_VARIANCE_MIN).length;
    return tooRegular / variances.length;
  }

  _scoreIdleStreak(profile) {
    // Multiple consecutive actions with zero mouse movement or no telemetry
    const recent = profile.actions.slice(-5);
    const allIdle = recent.every(t => !t || t.trailLength == null || t.trailLength < 2);
    if (recent.length >= 5 && allIdle) return 1;
    const latest = profile.actions[profile.actions.length - 1];
    if (!latest) return 0;
    const streak = latest.idleStreak || 0;
    if (streak >= 5) return 1;
    if (streak >= 3) return 0.6;
    return 0;
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────

function variance(arr) {
  if (arr.length < 2) return 0;
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  return arr.reduce((sum, v) => sum + Math.pow(v - mean, 2), 0) / arr.length;
}

module.exports = { BotDetector, THRESHOLDS };
