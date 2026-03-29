"use strict";

const { makeCard } = require("./types");

function createDeck(rng) {
  const cards = [];
  for (let suit = 1; suit <= 4; suit++) {
    for (let rank = 2; rank <= 14; rank++) {
      cards.push(makeCard(rank, suit));
    }
  }
  shuffle(cards, rng);
  return cards;
}

// Fisher-Yates shuffle. Optional rng function returns [0,1) for deterministic tests.
function shuffle(arr, rng) {
  const rand = rng || Math.random;
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function dealCards(deck, n) {
  if (deck.length < n) throw new Error(`Cannot deal ${n} cards from deck with ${deck.length}`);
  return deck.splice(0, n);
}

module.exports = { createDeck, dealCards, shuffle };
