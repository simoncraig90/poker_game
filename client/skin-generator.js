/**
 * Random poker client skin generator.
 *
 * Generates a unique visual theme for the poker table.
 * Loaded via ?skin=random or ?skin=random_42 (seeded).
 *
 * Randomizes:
 *   - Table felt color and gradient
 *   - Action button colors, shape, position (overlay vs bottom bar)
 *   - Card face rendering style (font, color, border)
 *   - Seat panel style (shape, color, opacity)
 *   - Pot display style
 *   - Background color
 *   - Card back design
 *   - Overall layout (portrait vs landscape proportions)
 */

const SkinGenerator = (function() {
  function seededRandom(seed) {
    let s = seed;
    return function() {
      s = (s * 1103515245 + 12345) & 0x7fffffff;
      return s / 0x7fffffff;
    };
  }

  function hsl(h, s, l) {
    return `hsl(${h}, ${s}%, ${l}%)`;
  }

  function randomColor(rng, hueRange, satRange, lightRange) {
    const h = hueRange[0] + rng() * (hueRange[1] - hueRange[0]);
    const s = satRange[0] + rng() * (satRange[1] - satRange[0]);
    const l = lightRange[0] + rng() * (lightRange[1] - lightRange[0]);
    return hsl(h, s, l);
  }

  function generate(seed) {
    const rng = seededRandom(seed || Date.now());

    // Felt color: green, blue, red, purple, or dark
    const feltHues = [
      [100, 150],  // green (classic)
      [200, 240],  // blue
      [0, 20],     // red
      [260, 300],  // purple
      [0, 360],    // any
    ];
    const feltHue = feltHues[Math.floor(rng() * feltHues.length)];
    const feltColor = randomColor(rng, feltHue, [30, 70], [15, 35]);
    const feltColorLight = randomColor(rng, feltHue, [30, 70], [20, 40]);

    // Background: dark
    const bgLightness = 5 + rng() * 20;
    const bgColor = hsl(0, 0, bgLightness);

    // Button style
    const buttonStyles = ['rounded', 'square', 'pill'];
    const buttonStyle = buttonStyles[Math.floor(rng() * buttonStyles.length)];
    const buttonRadius = buttonStyle === 'pill' ? 25 : buttonStyle === 'rounded' ? 10 : 3;

    // Button position: overlay on table or bottom bar
    const buttonPosition = rng() > 0.5 ? 'overlay' : 'bottom';

    // Fold button color: red variants
    const foldColor = randomColor(rng, [0, 15], [60, 100], [25, 50]);
    // Call button color: green or blue
    const callColor = randomColor(rng, [80 + rng() * 160, 80 + rng() * 160], [40, 80], [20, 45]);
    // Raise button: red, orange, or same as fold
    const raiseColor = rng() > 0.5 ? foldColor : randomColor(rng, [15, 45], [60, 100], [30, 50]);

    // Card style
    const cardBorderRadius = 2 + rng() * 8;
    const cardShadow = rng() > 0.3;
    const cardBorderColor = rng() > 0.5 ? '#333' : '#555';

    // Seat panel
    const panelOpacity = 0.6 + rng() * 0.35;
    const panelRadius = 2 + rng() * 10;
    const panelColor = `rgba(${Math.floor(rng() * 40)}, ${Math.floor(rng() * 40)}, ${Math.floor(rng() * 40)}, ${panelOpacity})`;

    // Stack color: green, white, or gold
    const stackColors = ['#5ce882', '#4ece78', '#ffd700', '#ffffff', '#00bcd4'];
    const stackColor = stackColors[Math.floor(rng() * stackColors.length)];

    // Pot display
    const potBgOpacity = 0.3 + rng() * 0.5;
    const potRadius = 5 + rng() * 15;

    // Table oval proportions
    const tableRadiusX = 40 + rng() * 15;  // 40-55%
    const tableRadiusY = 35 + rng() * 15;  // 35-50%

    // Hero card overlap
    const heroOverlap = 15 + rng() * 25;  // 15-40px

    // Font
    const fonts = [
      "'Roboto Condensed', sans-serif",
      "'Arial', sans-serif",
      "'Segoe UI', sans-serif",
      "'Verdana', sans-serif",
      "'Trebuchet MS', sans-serif",
    ];
    const font = fonts[Math.floor(rng() * fonts.length)];

    return {
      seed,
      feltColor,
      feltColorLight,
      bgColor,
      buttonStyle,
      buttonRadius,
      buttonPosition,
      foldColor,
      callColor,
      raiseColor,
      cardBorderRadius,
      cardShadow,
      cardBorderColor,
      panelColor,
      panelRadius,
      stackColor,
      potBgOpacity,
      potRadius,
      tableRadiusX,
      tableRadiusY,
      heroOverlap,
      font,
    };
  }

  function applySkin(skin) {
    const style = document.createElement('style');
    style.textContent = `
      body { font-family: ${skin.font}; background: ${skin.bgColor}; }
      #header { background: ${skin.bgColor}; }
      #table-area { background: ${skin.bgColor}; }

      #table-felt::before {
        background: radial-gradient(ellipse at center, ${skin.feltColorLight} 0%, ${skin.feltColor} 100%) !important;
        border-radius: ${skin.tableRadiusX}% / ${skin.tableRadiusY}% !important;
      }

      #fold-btn { background: ${skin.foldColor} !important; border-radius: ${skin.buttonRadius}px !important; }
      #call-btn, #check-btn { background: ${skin.callColor} !important; border-radius: ${skin.buttonRadius}px !important; }
      #raise-btn, #bet-btn { background: ${skin.raiseColor} !important; border-radius: ${skin.buttonRadius}px !important; }

      ${skin.buttonPosition === 'bottom' ? `
        #action-bar {
          position: fixed !important; bottom: 0 !important; left: 0 !important; right: 0 !important;
          top: auto !important; transform: none !important;
          background: rgba(0,0,0,0.9) !important; padding: 8px !important;
          width: 100% !important; max-width: 100% !important;
        }
      ` : ''}

      .seat { background: ${skin.panelColor} !important; border-radius: ${skin.panelRadius}px !important; }
      .seat-stack { color: ${skin.stackColor} !important; }

      .card { border-radius: ${skin.cardBorderRadius}px !important;
              border: 1px solid ${skin.cardBorderColor} !important;
              ${skin.cardShadow ? 'box-shadow: 2px 3px 8px rgba(0,0,0,0.5) !important;' : 'box-shadow: none !important;'}
      }

      .hero-cards .card + .card { margin-left: -${skin.heroOverlap}px !important; }

      #pot { background: rgba(0,0,0,${skin.potBgOpacity}) !important; border-radius: ${skin.potRadius}px !important; }
    `;
    document.head.appendChild(style);
    document.title = `Random Skin #${skin.seed}`;
    console.log(`[Skin] Applied random skin #${skin.seed}`, skin);
  }

  return { generate, applySkin };
})();

// Auto-apply if ?skin=random or ?skin=random_N in URL
(function() {
  const params = new URLSearchParams(window.location.search);
  const skinParam = params.get('skin');
  if (skinParam && skinParam.startsWith('random')) {
    const seed = skinParam.includes('_') ? parseInt(skinParam.split('_')[1]) : Math.floor(Math.random() * 100000);
    const skin = SkinGenerator.generate(seed);
    // Wait for DOM
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => SkinGenerator.applySkin(skin));
    } else {
      SkinGenerator.applySkin(skin);
    }
  }
})();
