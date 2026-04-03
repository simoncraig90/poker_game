#!/bin/bash
# Deploy poker server to Proxmox
set -e

REMOTE="root@proxmox"
REMOTE_DIR="/opt/poker-server"

echo "Syncing to $REMOTE:$REMOTE_DIR..."
rsync -avz --delete \
  --exclude node_modules \
  --exclude .git \
  --exclude data \
  --exclude captures \
  --exclude runs \
  --exclude test-output \
  --exclude screenshots \
  --exclude videos \
  --exclude 'vision/models' \
  --exclude '*.pt' \
  ./ "$REMOTE:$REMOTE_DIR/"

echo "Installing dependencies..."
ssh "$REMOTE" "cd $REMOTE_DIR && npm install --production"

echo "Restarting poker-server..."
ssh "$REMOTE" "systemctl restart poker-server"

echo "Done. Server status:"
ssh "$REMOTE" "systemctl status poker-server --no-pager -l"
