"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

/**
 * Annotation store — study notes tied to hands and replay frames.
 *
 * Storage: data/sessions/{sessionId}/annotations.jsonl
 * One JSON line per annotation. Append-only write, full-scan read.
 * Deletions are soft: a delete line with the annotation ID marks it removed.
 *
 * Annotation schema:
 * {
 *   id: string,              // unique ID (ann-...)
 *   sessionId: string,
 *   handId: string,
 *   frameIndex: number|null,  // null = whole-hand note
 *   street: string|null,      // street at the annotated frame
 *   tag: string,              // short category: "mistake", "interesting", "question", "good", "review"
 *   text: string,             // free-text note
 *   createdAt: string,        // ISO timestamp
 *   deleted: boolean,         // soft delete marker
 * }
 */

class AnnotationStore {
  constructor(sessionStorage) {
    this.sessionStorage = sessionStorage;
  }

  _filePath(sessionId) {
    const info = this.sessionStorage.load(sessionId);
    if (!info) return null;
    return path.join(info.dir, "annotations.jsonl");
  }

  _readAll(sessionId) {
    const filePath = this._filePath(sessionId);
    if (!filePath || !fs.existsSync(filePath)) return [];
    const content = fs.readFileSync(filePath, "utf8").trim();
    if (!content) return [];
    return content.split("\n").filter(Boolean).map((line) => JSON.parse(line));
  }

  /**
   * Get all live (non-deleted) annotations for a session+hand.
   */
  getForHand(sessionId, handId) {
    const all = this._readAll(sessionId);
    const deleted = new Set(all.filter((a) => a.deleted).map((a) => a.id));
    return all
      .filter((a) => a.handId === handId && !a.deleted && !deleted.has(a.id))
      .sort((a, b) => (a.createdAt || "").localeCompare(b.createdAt || ""));
  }

  /**
   * Add an annotation. Returns the created annotation.
   */
  add(sessionId, handId, { frameIndex, street, tag, text }) {
    const filePath = this._filePath(sessionId);
    if (!filePath) throw new Error(`Session not found: ${sessionId}`);

    const annotation = {
      id: "ann-" + crypto.randomUUID().slice(0, 12),
      sessionId,
      handId: String(handId),
      frameIndex: frameIndex != null ? frameIndex : null,
      street: street || null,
      tag: tag || "",
      text: text || "",
      createdAt: new Date().toISOString(),
      deleted: false,
    };

    fs.appendFileSync(filePath, JSON.stringify(annotation) + "\n");
    return annotation;
  }

  /**
   * Get annotation counts and tag sets per hand for a session.
   * Returns { handId: { count, tags: string[] } } for hands with at least 1 live annotation.
   * Single file scan.
   */
  getCountsByHand(sessionId) {
    const all = this._readAll(sessionId);
    const deleted = new Set(all.filter((a) => a.deleted).map((a) => a.id));
    const info = {};
    for (const a of all) {
      if (a.deleted || deleted.has(a.id) || !a.handId) continue;
      if (!info[a.handId]) info[a.handId] = { count: 0, tags: new Set() };
      info[a.handId].count++;
      if (a.tag) info[a.handId].tags.add(a.tag);
    }
    // Convert Sets to arrays for JSON serialization
    const result = {};
    for (const [hid, v] of Object.entries(info)) {
      result[hid] = { count: v.count, tags: [...v.tags] };
    }
    return result;
  }

  /**
   * Soft-delete an annotation by ID.
   */
  delete(sessionId, annotationId) {
    const filePath = this._filePath(sessionId);
    if (!filePath) return false;

    // Append a deletion marker
    const marker = { id: annotationId, deleted: true, _deletedAt: new Date().toISOString() };
    fs.appendFileSync(filePath, JSON.stringify(marker) + "\n");
    return true;
  }
}

module.exports = { AnnotationStore };
