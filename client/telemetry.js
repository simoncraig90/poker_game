// ═══════════════════════════════════════════════════════════════════════════
//  Client-side Telemetry Collector
//  Captures mouse movement, click precision, timing, and keyboard patterns
//  for bot detection. Attaches to PLAYER_ACTION commands automatically.
// ═══════════════════════════════════════════════════════════════════════════

(function () {
  "use strict";

  // ── State ─────────────────────────────────────────────────────────────

  const mouseTrail = [];           // {x, y, t} samples since last action
  const clickLog = [];             // {x, y, t, target} recent clicks
  const keystrokeTimes = [];       // timestamps of keystrokes in bet input
  let lastActionLegalAt = 0;       // when action buttons last became enabled
  let lastMouseMoveAt = 0;
  let tabFocused = true;
  let tabBlurCount = 0;
  let scrollCount = 0;
  let idleStreak = 0;              // consecutive actions with no mouse movement between
  const TRAIL_MAX = 500;           // max mouse samples to keep

  // ── Mouse tracking ──────────────────────────────────────────────────

  document.addEventListener("mousemove", (e) => {
    const now = Date.now();
    mouseTrail.push({ x: e.clientX, y: e.clientY, t: now });
    if (mouseTrail.length > TRAIL_MAX) mouseTrail.shift();
    lastMouseMoveAt = now;
  });

  document.addEventListener("click", (e) => {
    clickLog.push({
      x: e.clientX, y: e.clientY, t: Date.now(),
      target: e.target.id || e.target.className || e.target.tagName,
    });
    if (clickLog.length > 50) clickLog.shift();
  });

  // ── Keyboard tracking (bet input only) ──────────────────────────────

  const betInput = document.getElementById("bet-input");
  if (betInput) {
    betInput.addEventListener("keydown", () => {
      keystrokeTimes.push(Date.now());
      if (keystrokeTimes.length > 30) keystrokeTimes.shift();
    });
  }

  // ── Tab focus tracking ──────────────────────────────────────────────

  document.addEventListener("visibilitychange", () => {
    tabFocused = !document.hidden;
    if (document.hidden) tabBlurCount++;
  });

  window.addEventListener("blur", () => { tabFocused = false; tabBlurCount++; });
  window.addEventListener("focus", () => { tabFocused = true; });

  // ── Scroll tracking ────────────────────────────────────────────────

  document.addEventListener("scroll", () => { scrollCount++; });

  // ── Detect when action buttons become enabled ──────────────────────

  const actionBtnIds = ["fold-btn", "check-btn", "call-btn", "bet-btn", "raise-btn"];
  let prevDisabledState = {};

  function pollActionButtons() {
    for (const id of actionBtnIds) {
      const btn = document.getElementById(id);
      if (!btn) continue;
      const wasDisabled = prevDisabledState[id] !== false;
      const isEnabled = !btn.disabled;
      if (wasDisabled && isEnabled) {
        // At least one action button just became enabled — record the moment
        lastActionLegalAt = Date.now();
        break;
      }
      prevDisabledState[id] = btn.disabled;
    }
  }

  // Poll every 100ms to catch button state changes
  setInterval(pollActionButtons, 100);

  // ── Snapshot: collect telemetry for a single action ─────────────────

  function collectSnapshot(actionBtnId) {
    const now = Date.now();

    // Reaction time: how long between action becoming legal and clicking
    const reactionMs = lastActionLegalAt > 0 ? now - lastActionLegalAt : -1;

    // Mouse trail analysis for the period since last action became legal
    const relevantTrail = lastActionLegalAt > 0
      ? mouseTrail.filter(p => p.t >= lastActionLegalAt)
      : mouseTrail.slice(-100);

    const trailStats = analyzeTrail(relevantTrail);

    // Click precision: how close to the center of the button did they click?
    const btn = document.getElementById(actionBtnId);
    const clickPrecision = btn ? getClickPrecision(btn) : null;

    // Keystroke timing for bet input
    const keystrokeIntervals = [];
    for (let i = 1; i < keystrokeTimes.length; i++) {
      keystrokeIntervals.push(keystrokeTimes[i] - keystrokeTimes[i - 1]);
    }

    // Time since last mouse movement
    const timeSinceMouseMove = lastMouseMoveAt > 0 ? now - lastMouseMoveAt : -1;

    // Track if there was ANY mouse movement between actions
    const hadMouseMovement = relevantTrail.length > 2;
    if (!hadMouseMovement) idleStreak++;
    else idleStreak = 0;

    const snapshot = {
      // Timing
      reactionMs,
      timeSinceMouseMove,

      // Mouse movement
      trailLength: relevantTrail.length,
      trailDistance: trailStats.totalDistance,
      trailStraightness: trailStats.straightness,
      trailAvgSpeed: trailStats.avgSpeed,
      trailSpeedVariance: trailStats.speedVariance,
      trailDirectionChanges: trailStats.directionChanges,
      trailPauses: trailStats.pauses,

      // Click precision
      clickOffsetX: clickPrecision ? clickPrecision.offsetX : null,
      clickOffsetY: clickPrecision ? clickPrecision.offsetY : null,
      clickDistFromCenter: clickPrecision ? clickPrecision.distFromCenter : null,

      // Keyboard
      keystrokeCount: keystrokeIntervals.length,
      keystrokeAvgInterval: keystrokeIntervals.length > 0
        ? keystrokeIntervals.reduce((a, b) => a + b, 0) / keystrokeIntervals.length
        : null,
      keystrokeVariance: keystrokeIntervals.length > 1
        ? variance(keystrokeIntervals)
        : null,

      // Context
      tabFocused,
      tabBlurCount,
      scrollCount,
      idleStreak,

      // Timestamp
      ts: now,
    };

    // Clear per-action state
    keystrokeTimes.length = 0;

    return snapshot;
  }

  // ── Trail analysis ──────────────────────────────────────────────────

  function analyzeTrail(trail) {
    if (trail.length < 2) {
      return {
        totalDistance: 0, straightness: 0, avgSpeed: 0,
        speedVariance: 0, directionChanges: 0, pauses: 0,
      };
    }

    let totalDistance = 0;
    const speeds = [];
    let directionChanges = 0;
    let pauses = 0;
    let prevAngle = null;

    for (let i = 1; i < trail.length; i++) {
      const dx = trail[i].x - trail[i - 1].x;
      const dy = trail[i].y - trail[i - 1].y;
      const dt = trail[i].t - trail[i - 1].t;
      const dist = Math.sqrt(dx * dx + dy * dy);
      totalDistance += dist;

      if (dt > 0) speeds.push(dist / dt);
      if (dt > 200 && dist < 3) pauses++; // stopped for 200ms+

      const angle = Math.atan2(dy, dx);
      if (prevAngle !== null) {
        let delta = Math.abs(angle - prevAngle);
        if (delta > Math.PI) delta = 2 * Math.PI - delta;
        if (delta > 0.5) directionChanges++; // ~30 degree change
      }
      prevAngle = angle;
    }

    // Straightness: ratio of straight-line distance to total path distance
    const startEnd = Math.sqrt(
      Math.pow(trail[trail.length - 1].x - trail[0].x, 2) +
      Math.pow(trail[trail.length - 1].y - trail[0].y, 2)
    );
    const straightness = totalDistance > 0 ? startEnd / totalDistance : 0;

    const avgSpeed = speeds.length > 0 ? speeds.reduce((a, b) => a + b, 0) / speeds.length : 0;
    const speedVariance = speeds.length > 1 ? variance(speeds) : 0;

    return { totalDistance, straightness, avgSpeed, speedVariance, directionChanges, pauses };
  }

  // ── Click precision relative to button center ──────────────────────

  function getClickPrecision(btn) {
    const rect = btn.getBoundingClientRect();
    const centerX = rect.x + rect.width / 2;
    const centerY = rect.y + rect.height / 2;
    const lastClick = clickLog[clickLog.length - 1];
    if (!lastClick) return null;

    const offsetX = lastClick.x - centerX;
    const offsetY = lastClick.y - centerY;
    return {
      offsetX,
      offsetY,
      distFromCenter: Math.sqrt(offsetX * offsetX + offsetY * offsetY),
    };
  }

  // ── Stats helpers ──────────────────────────────────────────────────

  function variance(arr) {
    const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
    return arr.reduce((sum, v) => sum + Math.pow(v - mean, 2), 0) / arr.length;
  }

  // ── Public API ─────────────────────────────────────────────────────

  window.Telemetry = {
    collectSnapshot,
    getTrailLength: () => mouseTrail.length,
    getClickCount: () => clickLog.length,
    reset: () => {
      mouseTrail.length = 0;
      clickLog.length = 0;
      keystrokeTimes.length = 0;
      tabBlurCount = 0;
      scrollCount = 0;
      idleStreak = 0;
    },
  };
})();
