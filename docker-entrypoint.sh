#!/bin/sh

echo "Creating directories: DOWNLOAD_DIR=${DOWNLOAD_DIR}, STATE_DIR=${STATE_DIR}, TEMP_DIR=${TEMP_DIR}"
mkdir -p "${DOWNLOAD_DIR:-/downloads}" "${STATE_DIR:-.}" "${TEMP_DIR:-/tmp/lunarmetube}"

exec python3 app/main.py
