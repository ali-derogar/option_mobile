#!/bin/bash
# Build Android APK using Docker with a persistent debug keystore.
# Every build uses the same signing key so APK updates install over the existing app.
#
# Usage: ./build-android.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
IMAGE="darush-android"
OUTPUT_DIR="${ROOT}/output"
OUTPUT_APK="${OUTPUT_DIR}/app-debug.apk"
KEYSTORE_DIR="${ROOT}/android-keystore"
GRADLE_CACHE="${ROOT}/.docker-gradle"
LOCK_FILE="${ROOT}/.android-build.lock"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

docker_proxy_url() {
    local value="$1"
    value="${value//127.0.0.1/host.docker.internal}"
    value="${value//localhost/host.docker.internal}"
    printf '%s' "${value}"
}

DOCKER_HTTP_PROXY="${HTTP_PROXY:-${http_proxy:-}}"
DOCKER_HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy:-}}"
DOCKER_ALL_PROXY="${ALL_PROXY:-${all_proxy:-}}"
DOCKER_NO_PROXY="${NO_PROXY:-${no_proxy:-}}"

if [[ -n "${DOCKER_HTTP_PROXY}" ]]; then
    DOCKER_HTTP_PROXY="$(docker_proxy_url "${DOCKER_HTTP_PROXY}")"
fi
if [[ -n "${DOCKER_HTTPS_PROXY}" ]]; then
    DOCKER_HTTPS_PROXY="$(docker_proxy_url "${DOCKER_HTTPS_PROXY}")"
fi
if [[ -n "${DOCKER_ALL_PROXY}" ]]; then
    DOCKER_ALL_PROXY="$(docker_proxy_url "${DOCKER_ALL_PROXY}")"
fi

echo "=== Building Darush Android APK via Docker ==="

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed or is not available in PATH" >&2
    exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    echo "ERROR: another Android build is already running for this project." >&2
    echo "Wait for it to finish, or remove ${LOCK_FILE} if you are sure it is stale." >&2
    exit 1
fi

echo "Step 1: Building base Docker image (cached after first run)..."
docker build -f "${ROOT}/Dockerfile.android" -t "${IMAGE}" "${ROOT}"

mkdir -p "${KEYSTORE_DIR}" "${OUTPUT_DIR}" "${GRADLE_CACHE}"

if [[ ! -f "${KEYSTORE_DIR}/debug.keystore" ]]; then
    echo "Step 2: Creating persistent debug keystore (one-time)..."
    docker run --rm \
        --entrypoint keytool \
        -v "${KEYSTORE_DIR}:/root/.android" \
        "${IMAGE}" \
        -genkeypair -v \
        -keystore /root/.android/debug.keystore \
        -storepass android \
        -alias androiddebugkey \
        -keypass android \
        -keyalg RSA \
        -keysize 2048 \
        -validity 10000 \
        -dname "CN=Android Debug,O=Android,C=US"
    echo "Keystore saved to: ${KEYSTORE_DIR}/debug.keystore"
    echo ""
    echo "NOTE: If the app is already installed with a different signature,"
    echo "      uninstall it once on your phone, then install the new APK."
    echo ""
else
    echo "Step 2: Using existing keystore at ${KEYSTORE_DIR}/debug.keystore"
fi

echo "Step 3: Building APK..."
docker run --rm \
    --add-host=host.docker.internal:host-gateway \
    -e "HOST_UID=${HOST_UID}" \
    -e "HOST_GID=${HOST_GID}" \
    -e "HTTP_PROXY=${DOCKER_HTTP_PROXY}" \
    -e "HTTPS_PROXY=${DOCKER_HTTPS_PROXY}" \
    -e "ALL_PROXY=${DOCKER_ALL_PROXY}" \
    -e "NO_PROXY=${DOCKER_NO_PROXY}" \
    -e "http_proxy=${DOCKER_HTTP_PROXY}" \
    -e "https_proxy=${DOCKER_HTTPS_PROXY}" \
    -e "all_proxy=${DOCKER_ALL_PROXY}" \
    -e "no_proxy=${DOCKER_NO_PROXY}" \
    -v "${ROOT}:/app" \
    -v "${KEYSTORE_DIR}:/root/.android" \
    -v "${GRADLE_CACHE}:/root/.gradle" \
    -v "${OUTPUT_DIR}:/output" \
    "${IMAGE}"

if [[ ! -f "${OUTPUT_APK}" ]]; then
    echo "ERROR: APK not found at ${OUTPUT_APK}" >&2
    exit 1
fi

echo "=== Done! ==="
echo "APK saved to: ${OUTPUT_APK} ($(du -h "${OUTPUT_APK}" | cut -f1))"
echo "Signing key: ${KEYSTORE_DIR}/debug.keystore (keep this file to allow future updates)"
