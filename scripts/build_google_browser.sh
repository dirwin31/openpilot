#!/usr/bin/env bash
# Builds the Linux ARM64 browser runtime archive in .cache/google-browser. Does not install it.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/laptop_device_build.sh doctor
docker run --rm --platform linux/arm64 openpilot-local-arm64-builder:latest \
  sh -ec 'test "$(uname -sm)" = "Linux aarch64"; capnp --version | grep -q "version 1.0.1$"'
image=starpilot-google-browser:bookworm-arm64
output="$PWD/.cache/google-browser"
mkdir -p "$output"
docker build --platform linux/arm64 -f tools/laptop_device_build/Dockerfile.google-browser -t "$image" .
docker run --rm --platform linux/arm64 --network none \
  --mount "type=bind,src=$output,dst=/output" "$image" sh -ec '
    test "$(uname -sm)" = "Linux aarch64"
    /usr/lib/chromium/chromium --version > /output/version.txt
    cp /usr/local/share/google-browser-packages.tsv /output/packages.tsv
    tar -C / --exclude=./proc --exclude=./sys --exclude=./dev --exclude=./output \
      --exclude=./etc/hostname --exclude=./etc/hosts --exclude=./etc/resolv.conf \
      -czf /output/rootfs.tar.gz .
    sha256sum /output/rootfs.tar.gz | cut -d " " -f 1 > /output/rootfs.sha256
  '
docker image inspect "$image" --format '{{.Id}}' > "$output/image-id.txt"
printf 'Browser package: %s/rootfs.tar.gz\n' "$output"
