#!/bin/bash
set -euo pipefail
if [ ! -s /data/configdb/state-key ]; then
  openssl rand -base64 756 > /data/configdb/state-key
fi
chmod 400 /data/configdb/state-key
chown mongodb:mongodb /data/configdb/state-key
exec /usr/local/bin/docker-entrypoint.sh mongod --replSet state-rs --keyFile /data/configdb/state-key --bind_ip_all
