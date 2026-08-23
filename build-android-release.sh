#!/bin/bash
# Build officially signed Android release artifacts using Docker.
#
# Usage:
#   ./build-android-release.sh
#
# The first run creates android-keystore/release.jks and
# android-keystore/release.properties. Keep both files private and backed up.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
IMAGE="options-android"
OUTPUT_DIR="${ROOT}/output"
KEYSTORE_DIR="${ROOT}/android-keystore"
KEYSTORE_FILE="${KEYSTORE_DIR}/release.jks"
PROPERTIES_FILE="${KEYSTORE_DIR}/release.properties"
DOCKER_ENV_FILE="${KEYSTORE_DIR}/release.docker.env"
GRADLE_CACHE="${ROOT}/.docker-gradle"
LOCK_FILE="${ROOT}/.android-release-build.lock"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

docker_proxy_url() {
    local value="$1"
    value="${value//127.0.0.1/host.docker.internal}"
    value="${value//localhost/host.docker.internal}"
    printf '%s' "${value}"
}

shell_quote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

random_password() {
    openssl rand -base64 32 | tr -d '\n'
}

write_release_properties() {
    local store_password="$1"
    local key_password="$2"
    local alias="$3"

    {
        printf 'RELEASE_KEYSTORE=/root/.android/release.jks\n'
        printf 'RELEASE_KEY_ALIAS=%s\n' "$(shell_quote "${alias}")"
        printf 'RELEASE_STORE_PASSWORD=%s\n' "$(shell_quote "${store_password}")"
        printf 'RELEASE_KEY_PASSWORD=%s\n' "$(shell_quote "${key_password}")"
    } > "${PROPERTIES_FILE}"
    chmod 600 "${PROPERTIES_FILE}"
}

write_docker_env_file() {
    {
        printf 'RELEASE_KEYSTORE=%s\n' "${RELEASE_KEYSTORE}"
        printf 'RELEASE_KEY_ALIAS=%s\n' "${RELEASE_KEY_ALIAS}"
        printf 'RELEASE_STORE_PASSWORD=%s\n' "${RELEASE_STORE_PASSWORD}"
        printf 'RELEASE_KEY_PASSWORD=%s\n' "${RELEASE_KEY_PASSWORD}"
    } > "${DOCKER_ENV_FILE}"
    chmod 600 "${DOCKER_ENV_FILE}"
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

echo "=== Building signed Options Android release via Docker ==="

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed or is not available in PATH" >&2
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl is required to generate release passwords" >&2
    exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    echo "ERROR: another Android release build is already running for this project." >&2
    echo "Wait for it to finish, or remove ${LOCK_FILE} if you are sure it is stale." >&2
    exit 1
fi

echo "Step 1: Building base Docker image (cached after first run)..."
docker build -f "${ROOT}/Dockerfile.android" -t "${IMAGE}" "${ROOT}"

mkdir -p "${KEYSTORE_DIR}" "${OUTPUT_DIR}" "${GRADLE_CACHE}"

if [[ ! -f "${KEYSTORE_FILE}" ]]; then
    echo "Step 2: Creating official release keystore (one-time)..."
    STORE_PASSWORD="$(random_password)"
    KEY_PASSWORD="${STORE_PASSWORD}"
    RELEASE_ALIAS="options-release"

    keytool -genkeypair -v \
        -keystore "${KEYSTORE_FILE}" \
        -storepass "${STORE_PASSWORD}" \
        -alias "${RELEASE_ALIAS}" \
        -keypass "${KEY_PASSWORD}" \
        -keyalg RSA \
        -keysize 4096 \
        -validity 10000 \
        -dname "CN=Options Release,O=TSETMC,C=IR"

    chmod 600 "${KEYSTORE_FILE}"
    write_release_properties "${STORE_PASSWORD}" "${KEY_PASSWORD}" "${RELEASE_ALIAS}"
    echo "Release keystore saved to: ${KEYSTORE_FILE}"
    echo "Release credentials saved to: ${PROPERTIES_FILE}"
else
    echo "Step 2: Using existing release keystore at ${KEYSTORE_FILE}"
    if [[ ! -f "${PROPERTIES_FILE}" ]]; then
        echo "ERROR: ${PROPERTIES_FILE} is missing." >&2
        echo "It must define RELEASE_KEYSTORE, RELEASE_KEY_ALIAS, RELEASE_STORE_PASSWORD, and RELEASE_KEY_PASSWORD." >&2
        exit 1
    fi
fi

set -a
# shellcheck disable=SC1090
source "${PROPERTIES_FILE}"
set +a
write_docker_env_file

echo "Step 3: Building signed release APK and AAB..."
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
    --env-file "${DOCKER_ENV_FILE}" \
    -v "${ROOT}:/app" \
    -v "${KEYSTORE_DIR}:/root/.android" \
    -v "${GRADLE_CACHE}:/root/.gradle" \
    -v "${OUTPUT_DIR}:/output" \
    --entrypoint /usr/local/bin/android-release-build.sh \
    "${IMAGE}"

if [[ ! -f "${OUTPUT_DIR}/tiger-options-release.apk" ]]; then
    echo "ERROR: release APK not found at ${OUTPUT_DIR}/tiger-options-release.apk" >&2
    exit 1
fi

echo "=== Done! ==="
echo "Release APK: ${OUTPUT_DIR}/tiger-options-release.apk ($(du -h "${OUTPUT_DIR}/tiger-options-release.apk" | cut -f1))"
if [[ -f "${OUTPUT_DIR}/tiger-options-release.aab" ]]; then
    echo "Release AAB: ${OUTPUT_DIR}/tiger-options-release.aab ($(du -h "${OUTPUT_DIR}/tiger-options-release.aab" | cut -f1))"
fi
echo "Signing key: ${KEYSTORE_FILE}"
echo "Keep ${KEYSTORE_FILE} and ${PROPERTIES_FILE} private and backed up."
