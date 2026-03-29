"use strict";

const fs = require("fs");

class EventLog {
  /**
   * @param {string|null} filePath - path to JSONL file
   * @param {boolean} loadExisting - if true, load events from existing file instead of truncating
   */
  constructor(filePath, loadExisting = false) {
    this.filePath = filePath;
    this.events = [];

    if (filePath) {
      if (loadExisting && fs.existsSync(filePath)) {
        const content = fs.readFileSync(filePath, "utf8").trim();
        if (content) {
          this.events = content.split("\n").filter(Boolean).map((line) => JSON.parse(line));
        }
      } else {
        fs.writeFileSync(filePath, "");
      }
    }
  }

  append(event) {
    this.events.push(event);
    if (this.filePath) {
      fs.appendFileSync(this.filePath, JSON.stringify(event) + "\n");
    }
    return event;
  }

  getEvents() {
    return this.events;
  }

  getHandEvents(handId) {
    return this.events.filter((e) => e.handId === handId);
  }
}

module.exports = { EventLog };
