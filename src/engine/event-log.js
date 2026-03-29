"use strict";

const fs = require("fs");

class EventLog {
  constructor(filePath) {
    this.filePath = filePath;
    this.events = [];
    if (filePath) {
      // Ensure file exists (truncate if new session)
      fs.writeFileSync(filePath, "");
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
