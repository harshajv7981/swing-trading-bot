#!/bin/zsh
# Copy the forward-test records that git ignores (and so exist once, on this Mac) to iCloud Drive.
#
#   scripts/backup_state.sh              # to ~/Library/Mobile Documents/com~apple~CloudDocs/swingbot-backup
#   SWINGBOT_BACKUP_DIR=/some/dir scripts/backup_state.sh
#
# What: data/state (M6 and paper ledgers, agent calls, Dhan books), runs/trading-agents (large-cap
# calls), runs/agents-trial (mid-cap briefs). Not alpaca_raw.jsonl (account details; the US trial
# keeps its state on the us-trial-state branch). Nothing is published: iCloud Drive is private.
#
# <dest>/latest is a mirror that never deletes. A file that changed or was removed here keeps its
# older copy in <dest>/changed/<date>/, so a bad overwrite is not copied over the only good one.
# Weekly runs it at the end of runs/agents-trial/weekly.sh; run it by hand after `swingbot-momentum update`.
set -eu
REPO=${0:A:h:h}
DEST=${SWINGBOT_BACKUP_DIR:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/swingbot-backup}
[[ -d ${DEST:h} ]] || { echo "backup: ${DEST:h} does not exist; set SWINGBOT_BACKUP_DIR" >&2; exit 1; }
cd "$REPO"
today=$(date +%F)
mkdir -p "$DEST/latest" "$DEST/changed/$today"
rsync -a --backup --backup-dir="$DEST/changed/$today" \
  --exclude alpaca_raw.jsonl --exclude .DS_Store \
  data/state runs/trading-agents runs/agents-trial "$DEST/latest/"
rmdir "$DEST/changed/$today" 2>/dev/null || true
echo "backup: $(find "$DEST/latest" -type f | wc -l | tr -d ' ') files in $DEST/latest"
