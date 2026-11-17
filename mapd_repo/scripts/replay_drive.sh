#!/usr/bin/env bash
# Road-matching replay harness: see mapd_repo/MATCHING.md, "Replaying a drive".
#
#   replay_drive.sh run       <dump.jsonl> <tile root> [frames.jsonl]   unmodified matcher; reproduction gate enforced
#   replay_drive.sh candidate <dump.jsonl> <tile root> [frames.jsonl]   changed matcher; gate reported only
#   replay_drive.sh report    <a.frames.jsonl>[,b.frames.jsonl...] [summary.json]
#   replay_drive.sh compare   <base frames,...> <candidate frames,...> [verdict.json]
#
# Runs Go in the pinned mapd builder image with the offline module cache, like ./build --mapd.
# Data paths may be anywhere; each one's folder is mounted into the container.
set -euo pipefail

MAPD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT_DIR="$(dirname "${MAPD_DIR}")"
MODULE_CACHE="${COMMA_MAPD_MODULE_CACHE:-${ROOT_DIR}/.cache/mapd-go-modules}"
IMAGE="${COMMA_MAPD_BUILD_IMAGE:-openpilot-local-mapd-builder:go1.25.1}"
GO_CACHE="${REPLAY_GO_CACHE:-${TMPDIR:-/tmp}/mapd-replay-gocache}"

usage() { sed -n '4,7p' "${BASH_SOURCE[0]}" | sed 's/^# *//' >&2; exit 2; }
[[ $# -ge 2 ]] || usage
[[ -d "${MODULE_CACHE}" ]] || { echo "ERROR: offline Go module cache missing: ${MODULE_CACHE}; set COMMA_MAPD_MODULE_CACHE" >&2; exit 1; }
mkdir -p "${GO_CACHE}"

mounts=() envs=() count=0
# container_path <host path>: mounts the path's folder and prints the path inside the container.
container_path() {
  local dir base
  dir="$(cd "$(dirname "$1")" && pwd -P)" base="$(basename "$1")"
  mounts+=(-v "${dir}:/m${count}")
  CONTAINER_PATH="/m${count}/${base}"
  count=$((count + 1))
}
container_list() {
  local out=() item
  IFS=',' read -r -a items <<< "$1"
  for item in "${items[@]}"; do container_path "${item}"; out+=("${CONTAINER_PATH}"); done
  CONTAINER_PATH="$(IFS=','; echo "${out[*]}")"
}

mode="$1"
case "${mode}" in
  run|candidate)
    [[ $# -ge 3 ]] || usage
    container_path "$2"; envs+=(-e "REPLAY=${CONTAINER_PATH}")
    [[ -d "$3" ]] || { echo "ERROR: tile root $3 is not a folder" >&2; exit 1; }
    mounts+=(-v "$(cd "$3" && pwd -P):/tiles:ro"); envs+=(-e REPLAY_TILES=/tiles)
    out="${4:-${2%.jsonl}.frames.jsonl}"
    touch "${out}"; container_path "${out}"; envs+=(-e "REPLAY_OUT=${CONTAINER_PATH}")
    [[ "${mode}" == candidate ]] && envs+=(-e REPLAY_GATE=off)
    test_name='TestReplayDrive$'
    ;;
  report)
    container_list "$2"; envs+=(-e "REPLAY_FRAMES=${CONTAINER_PATH}")
    if [[ $# -ge 3 ]]; then touch "$3"; container_path "$3"; envs+=(-e "REPLAY_SUMMARY=${CONTAINER_PATH}"); fi
    test_name='TestReplayReport$'
    ;;
  compare)
    [[ $# -ge 3 ]] || usage
    container_list "$2"; envs+=(-e "REPLAY_BASE=${CONTAINER_PATH}")
    container_list "$3"; envs+=(-e "REPLAY_CAND=${CONTAINER_PATH}")
    if [[ $# -ge 4 ]]; then touch "$4"; container_path "$4"; envs+=(-e "REPLAY_COMPARE_OUT=${CONTAINER_PATH}"); fi
    test_name='TestReplayCompare$'
    ;;
  *) usage ;;
esac
for name in REPLAY_MIN_AGREE REPLAY_MAX_WRONG_LIMIT_DELTA REPLAY_MAX_OFFROAD_DELTA REPLAY_MAX_P99_RATIO; do
  [[ -n "${!name:-}" ]] && envs+=(-e "${name}=${!name}")
done

docker run --rm -v "${ROOT_DIR}:/src:ro" -v "${MODULE_CACHE}:/gomodcache:ro" -v "${GO_CACHE}:/gocache" "${mounts[@]}" \
  -e GOTOOLCHAIN=local -e GOPROXY=off -e GOSUMDB=off -e GOMODCACHE=/gomodcache -e GOCACHE=/gocache -e GOFLAGS=-mod=readonly \
  "${envs[@]}" -w /src/mapd_repo "${IMAGE}" \
  go test -run "${test_name}" -count=1 -v -timeout 60m . 2>&1 | grep -v -e 'Loading bounds file' -e '^=== RUN'
