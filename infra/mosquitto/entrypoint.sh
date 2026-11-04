#!/bin/sh
# Creates the password file from environment variables, renders the
# configuration and starts Mosquitto. Passwords never live in the image.
set -eu
CONFIG=/mosquitto/config
DATA=/mosquitto/data
mkdir -p "$CONFIG" "$DATA"
: > "$CONFIG/passwords"
mosquitto_passwd -b "$CONFIG/passwords" gw-01 "$MQTT_GW01_PASSWORD"
mosquitto_passwd -b "$CONFIG/passwords" hub "$MQTT_HUB_PASSWORD"
mosquitto_passwd -b "$CONFIG/passwords" explorer "$MQTT_EXPLORER_PASSWORD"
cp /vigiles/acl "$CONFIG/acl"
sed -e "s|@CONFIG@|$CONFIG|g" -e "s|@DATA@|$DATA|g" /vigiles/mosquitto.conf.template > "$CONFIG/mosquitto.conf"
chown -R mosquitto:mosquitto "$CONFIG" "$DATA" 2>/dev/null || true
chmod 0700 "$CONFIG/passwords"
exec mosquitto -c "$CONFIG/mosquitto.conf"
