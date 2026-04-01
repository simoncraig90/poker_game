"use strict";

/**
 * Bot detection module.
 *
 * Analyzes player behavior over time and produces a "humanity score"
 * (0 = definitely bot, 1 = definitely human).
 *
 * Passive monitoring only — never blocks or rejects actions.
 */

const MIN_ACTIONS_FOR_SCORE = 30;
const ROLLING_DECAY = 0.98; // exponential decay for older observations
const EXACT_FRACTION_TOLERANCE = 0.005; // 0.5% tolerance for "exact" pot fractions

// Common bot bet fractions to check against
const BOT_FRACTIONS = [1 / 3, 1 / 2, 2 / 3, 3 / 4, 1, 1.5, 2];

// ── Statistical helpers ───────────────────────────────────────────────────

function mean(arr) {
  if (arr.length === 0) return 0;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function stddev(arr) {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  const variance = arr.reduce((sum, x) => sum + (x - m) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}

function coefficientOfVariation(arr) {
  const m = mean(arr);
  if (m === 0) return 0;
  return stddev(arr) / m;
}

/**
 * Skewness (Fisher): measures asymmetry of distribution.
 * Human response times are right-skewed (log-normal).
 */
function skewness(arr) {
  if (arr.length < 3) return 0;
  const n = arr.length;
  const m = mean(arr);
  const s = stddev(arr);
  if (s === 0) return 0;
  const sum = arr.reduce((acc, x) => acc + ((x - m) / s) ** 3, 0);
  return (n / ((n - 1) * (n - 2))) * sum;
}

/**
 * Excess kurtosis: measures tail heaviness.
 * Normal distribution has excess kurtosis = 0.
 */
function kurtosis(arr) {
  if (arr.length < 4) return 0;
  const n = arr.length;
  const m = mean(arr);
  const s = stddev(arr);
  if (s === 0) return 0;
  const sum = arr.reduce((acc, x) => acc + ((x - m) / s) ** 4, 0);
  const raw = (n * (n + 1)) / ((n - 1) * (n - 2) * (n - 3)) * sum;
  const correction = (3 * (n - 1) ** 2) / ((n - 2) * (n - 3));
  return raw - correction;
}

/**
 * Shannon entropy of a discrete distribution (array of counts).
 * Higher = more random/varied. Lower = more predictable.
 */
function entropy(counts) {
  const total = counts.reduce((a, b) => a + b, 0);
  if (total === 0) return 0;
  let h = 0;
  for (const c of counts) {
    if (c > 0) {
      const p = c / total;
      h -= p * Math.log2(p);
    }
  }
  return h;
}

/**
 * Apply exponential decay weighting: recent values get more weight.
 * Returns a new array where each element is weighted.
 */
function applyDecay(arr, decay) {
  const n = arr.length;
  return arr.map((v, i) => v * decay ** (n - 1 - i));
}

// ── Player tracker ────────────────────────────────────────────────────────

function createPlayerData() {
  return {
    // Raw response times in ms
    responseTimes: [],

    // Action counts by situation key -> action -> count
    // Situation key encodes phase + simplified hand strength bucket
    situationActions: {},

    // Session tracking
    sessions: [],
    currentSessionStart: null,
    handsThisSession: 0,
    handsPerHourSamples: [],

    // VPIP/PFR rolling windows
    preflopDecisions: [], // { vpip: bool, pfr: bool }

    // Bet sizing: array of { fraction, potSize }
    betSizings: [],

    // Total action count
    totalActions: 0,
  };
}

// ── BotDetector class ─────────────────────────────────────────────────────

class BotDetector {
  constructor() {
    this.players = {};
  }

  _getPlayer(name) {
    if (!this.players[name]) {
      this.players[name] = createPlayerData();
    }
    return this.players[name];
  }

  /**
   * Record a player action for analysis.
   *
   * @param {string} playerName
   * @param {string} action       - FOLD, CHECK, CALL, BET, RAISE
   * @param {number} amount       - bet/raise amount (0 for fold/check)
   * @param {number} responseTimeMs - time between prompt and response
   * @param {string} handStrength - simplified bucket: "trash", "weak", "medium", "strong", "premium"
   * @param {number} potSize      - current pot size
   * @param {string} phase        - PREFLOP, FLOP, TURN, RIVER
   */
  recordAction(playerName, action, amount, responseTimeMs, handStrength, potSize, phase) {
    const p = this._getPlayer(playerName);
    p.totalActions++;

    // 1. Response time
    if (typeof responseTimeMs === "number" && responseTimeMs > 0) {
      p.responseTimes.push(responseTimeMs);
    }

    // 2. Decision consistency (action per situation)
    const sitKey = `${phase}:${handStrength || "unknown"}`;
    if (!p.situationActions[sitKey]) p.situationActions[sitKey] = {};
    const sitActions = p.situationActions[sitKey];
    sitActions[action] = (sitActions[action] || 0) + 1;

    // 3. VPIP/PFR tracking (preflop only, exclude blinds)
    if (phase === "PREFLOP" && action !== "BLIND_SB" && action !== "BLIND_BB") {
      const vpip = action !== "FOLD";
      const pfr = action === "RAISE" || action === "BET";
      p.preflopDecisions.push({ vpip, pfr });
    }

    // 4. Bet sizing
    if ((action === "BET" || action === "RAISE") && amount > 0 && potSize > 0) {
      p.betSizings.push({ fraction: amount / potSize, potSize });
    }

    // 5. Session tracking
    if (!p.currentSessionStart) {
      p.currentSessionStart = Date.now();
    }
    p.handsThisSession++;
  }

  /**
   * Record end of a session for a player.
   */
  recordSessionEnd(playerName) {
    const p = this._getPlayer(playerName);
    if (p.currentSessionStart) {
      const durationMs = Date.now() - p.currentSessionStart;
      const durationHours = durationMs / (1000 * 60 * 60);
      p.sessions.push({
        start: p.currentSessionStart,
        end: Date.now(),
        durationMs,
        hands: p.handsThisSession,
      });
      if (durationHours > 0) {
        p.handsPerHourSamples.push(p.handsThisSession / durationHours);
      }
    }
    p.currentSessionStart = null;
    p.handsThisSession = 0;
  }

  // ── Signal calculations ───────────────────────────────────────────────

  /**
   * Signal A: Action timing consistency.
   * Low CV (coefficient of variation) = suspicious.
   * Returns 0 (bot-like) to 1 (human-like).
   */
  _scoreTiming(p) {
    if (p.responseTimes.length < 10) return null;
    const times = p.responseTimes;
    const cv = coefficientOfVariation(times);

    // Humans typically have CV > 0.5. Bots < 0.2.
    // Linear interpolation between 0.1 (bot) and 0.6 (human)
    return Math.min(1, Math.max(0, (cv - 0.1) / 0.5));
  }

  /**
   * Signal B: Timing distribution shape.
   * Humans have right-skewed (log-normal) timing.
   * Returns 0 (bot-like) to 1 (human-like).
   */
  _scoreTimingDistribution(p) {
    if (p.responseTimes.length < 20) return null;
    const times = p.responseTimes;
    const sk = skewness(times);
    const kt = kurtosis(times);

    // Humans: positive skewness (0.5-3), moderate kurtosis (0-5)
    // Bots: near-zero skewness and kurtosis (too regular)
    let skScore = 0;
    if (sk > 0.3) {
      skScore = Math.min(1, sk / 2); // more skew = more human
    } else if (sk < -0.3) {
      skScore = 0; // negative skew is unusual
    } else {
      skScore = 0.3; // near-zero skew is mildly suspicious
    }

    // Kurtosis near zero with near-zero skewness = too perfect
    let ktScore = 1;
    if (Math.abs(sk) < 0.2 && Math.abs(kt) < 0.5) {
      ktScore = 0.2; // suspiciously normal
    }

    return skScore * 0.7 + ktScore * 0.3;
  }

  /**
   * Signal C: Decision consistency.
   * Bots always do the same thing in the same situation.
   * Returns 0 (bot-like) to 1 (human-like).
   */
  _scoreDecisionConsistency(p) {
    const situations = Object.values(p.situationActions);
    if (situations.length < 3) return null;

    // Only consider situations with enough data
    const qualified = situations.filter((acts) => {
      const total = Object.values(acts).reduce((a, b) => a + b, 0);
      return total >= 5;
    });
    if (qualified.length < 2) return null;

    // Calculate average entropy across situations
    const entropies = qualified.map((acts) => {
      const counts = Object.values(acts);
      return entropy(counts);
    });

    const avgEntropy = mean(entropies);
    // Max possible entropy for 5 actions = log2(5) ≈ 2.32
    // Humans: entropy > 0.5. Bots: entropy < 0.2.
    return Math.min(1, Math.max(0, avgEntropy / 1.0));
  }

  /**
   * Signal D: Session patterns.
   * Bots play indefinitely with consistent pace.
   * Returns 0 (bot-like) to 1 (human-like).
   */
  _scoreSessionPatterns(p) {
    if (p.sessions.length < 2) return null;

    // Check session duration variance
    const durations = p.sessions.map((s) => s.durationMs);
    const durationCV = coefficientOfVariation(durations);

    // Check hands-per-hour consistency
    let hphScore = 0.5;
    if (p.handsPerHourSamples.length >= 2) {
      const hphCV = coefficientOfVariation(p.handsPerHourSamples);
      // Humans: HPH varies a lot (CV > 0.3). Bots: consistent (CV < 0.1)
      hphScore = Math.min(1, Math.max(0, (hphCV - 0.05) / 0.35));
    }

    // Session duration variance: humans vary, bots are consistent
    const durScore = Math.min(1, Math.max(0, (durationCV - 0.1) / 0.5));

    return durScore * 0.5 + hphScore * 0.5;
  }

  /**
   * Signal E: VPIP/PFR stability across rolling windows.
   * Humans fluctuate session-to-session. Bots are steady.
   * Returns 0 (bot-like) to 1 (human-like).
   */
  _scoreVpipPfrStability(p) {
    const decisions = p.preflopDecisions;
    if (decisions.length < 100) return null; // need at least 2 windows of 50

    // Calculate rolling VPIP/PFR over 50-hand windows
    const windowSize = 50;
    const vpipWindows = [];
    const pfrWindows = [];

    for (let i = 0; i <= decisions.length - windowSize; i += 25) {
      const window = decisions.slice(i, i + windowSize);
      const vpip = window.filter((d) => d.vpip).length / windowSize;
      const pfr = window.filter((d) => d.pfr).length / windowSize;
      vpipWindows.push(vpip);
      pfrWindows.push(pfr);
    }

    if (vpipWindows.length < 2) return null;

    const vpipSD = stddev(vpipWindows);
    const pfrSD = stddev(pfrWindows);

    // Humans: SD > 0.05. Bots: SD < 0.02.
    const vpipScore = Math.min(1, Math.max(0, (vpipSD - 0.01) / 0.08));
    const pfrScore = Math.min(1, Math.max(0, (pfrSD - 0.01) / 0.08));

    return vpipScore * 0.5 + pfrScore * 0.5;
  }

  /**
   * Signal F: Bet sizing patterns.
   * Bots use exact pot fractions. Humans use round numbers or approximate.
   * Returns 0 (bot-like) to 1 (human-like).
   */
  _scoreBetSizing(p) {
    if (p.betSizings.length < 10) return null;

    let exactCount = 0;
    for (const { fraction } of p.betSizings) {
      for (const botFrac of BOT_FRACTIONS) {
        if (Math.abs(fraction - botFrac) < EXACT_FRACTION_TOLERANCE) {
          exactCount++;
          break;
        }
      }
    }

    const exactRatio = exactCount / p.betSizings.length;
    // Humans: < 30% exact. Bots: > 80% exact.
    return Math.min(1, Math.max(0, 1 - (exactRatio - 0.2) / 0.6));
  }

  // ── Public API ────────────────────────────────────────────────────────

  /**
   * Get humanity score for a player.
   *
   * @param {string} playerName
   * @returns {{ score: number, signals: Object, suspicious: boolean, reliable: boolean }}
   */
  getScore(playerName) {
    const p = this.players[playerName];
    if (!p) {
      return { score: 1, signals: {}, suspicious: false, reliable: false };
    }

    const reliable = p.totalActions >= MIN_ACTIONS_FOR_SCORE;

    const signals = {
      timing: this._scoreTiming(p),
      timingDistribution: this._scoreTimingDistribution(p),
      decisionConsistency: this._scoreDecisionConsistency(p),
      sessionPatterns: this._scoreSessionPatterns(p),
      vpipPfrStability: this._scoreVpipPfrStability(p),
      betSizing: this._scoreBetSizing(p),
    };

    // Weighted average of available signals
    const weights = {
      timing: 3,
      timingDistribution: 2,
      decisionConsistency: 3,
      sessionPatterns: 1,
      vpipPfrStability: 2,
      betSizing: 2,
    };

    let weightedSum = 0;
    let totalWeight = 0;
    for (const [key, value] of Object.entries(signals)) {
      if (value !== null) {
        weightedSum += value * weights[key];
        totalWeight += weights[key];
      }
    }

    const score = totalWeight > 0 ? weightedSum / totalWeight : 1;

    return {
      score: Math.round(score * 1000) / 1000,
      signals,
      suspicious: reliable && score < 0.4,
      reliable,
      totalActions: p.totalActions,
    };
  }

  /**
   * Get summary scores for all tracked players.
   * @returns {Array<{ player: string, score: number, suspicious: boolean, reliable: boolean }>}
   */
  getSummary() {
    return Object.keys(this.players).map((name) => {
      const result = this.getScore(name);
      return { player: name, ...result };
    });
  }

  /**
   * Get raw tracking data for manual review.
   * @param {string} playerName
   * @returns {Object|null}
   */
  getRawData(playerName) {
    return this.players[playerName] || null;
  }
}

module.exports = { BotDetector };
