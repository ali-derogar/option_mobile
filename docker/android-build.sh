#!/bin/bash
# Run inside Docker: sync sources and build a debug APK with the mounted keystore.
set -euo pipefail

cd /app
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export GRADLE_USER_HOME=/root/.gradle

HOST_UID="${HOST_UID:-0}"
HOST_GID="${HOST_GID:-0}"

cleanup_permissions() {
    if [[ "${HOST_UID}" != "0" ]] && [[ "${HOST_GID}" != "0" ]]; then
        chown -R "${HOST_UID}:${HOST_GID}" /app/build /app/logs /output /root/.android /root/.gradle 2>/dev/null || true
    fi
}
trap cleanup_permissions EXIT

stop_gradle_daemons() {
    local gradle_dir="/app/build/darush/android/gradle"
    if [[ -x "${gradle_dir}/gradlew" ]]; then
        (cd "${gradle_dir}" && ./gradlew --stop >/dev/null 2>&1) || true
    fi
    pkill -f 'GradleDaemon' >/dev/null 2>&1 || true
}

clean_stale_gradle_locks() {
    stop_gradle_daemons
    rm -rf /app/build/darush/android/gradle/.gradle 2>/dev/null || true
    if [[ -d "${GRADLE_USER_HOME}/caches" ]]; then
        find "${GRADLE_USER_HOME}/caches" -type f -name "*.lock" -delete 2>/dev/null || true
    fi
}

patch_gradle_properties() {
    local props="/app/build/darush/android/gradle/gradle.properties"
    [[ -f "${props}" ]] || return 0
    grep -q '^org\.gradle\.daemon=false$' "${props}" || printf '\norg.gradle.daemon=false\n' >> "${props}"
    grep -q '^org\.gradle\.vfs\.watch=false$' "${props}" || printf 'org.gradle.vfs.watch=false\n' >> "${props}"
    grep -q '^org\.gradle\.parallel=false$' "${props}" || printf 'org.gradle.parallel=false\n' >> "${props}"
}

patch_pip_options() {
    local opts="/app/build/darush/android/gradle/app/pip-options.txt"
    local tmp
    [[ -f "${opts}" ]] || return 0
    tmp="$(mktemp)"
    {
        printf '%s\n' \
            "--index-url" \
            "https://mirrors.aliyun.com/pypi/simple/" \
            "--extra-index-url" \
            "https://chaquo.com/pypi-13.1" \
            "--trusted-host" \
            "mirrors.aliyun.com"
        grep -v -x \
            -e "--index-url" \
            -e "https://mirrors.aliyun.com/pypi/simple/" \
            -e "--extra-index-url" \
            -e "https://chaquo.com/pypi-13.1" \
            -e "--trusted-host" \
            -e "mirrors.aliyun.com" \
            "${opts}"
    } > "${tmp}"
    mv "${tmp}" "${opts}"
}

if [[ ! -f /root/.android/debug.keystore ]]; then
    echo "ERROR: debug.keystore not found at /root/.android/debug.keystore" >&2
    echo "Run ./build-android.sh from the host to create the persistent keystore." >&2
    exit 1
fi

ANDROID_DIR="/app/build/darush/android"
if [[ ! -d "${ANDROID_DIR}" ]]; then
    echo "Creating Android Gradle project..."
    briefcase create android --no-input
fi

clean_stale_gradle_locks

# Chaquopy / SDK compatibility patches
rm -rf /app/build/darush/android/gradle/app/src/main/res/values-v35

find /app/build -type f -name "*.gradle" | while read -r f; do
    sed -i -E \
        -e 's|google\(\)|maven { url "https://maven.aliyun.com/repository/google" }|g' \
        -e 's|mavenCentral\(\)|maven { url "https://maven.aliyun.com/repository/central" }|g' \
        -e 's|gradlePluginPortal\(\)|maven { url "https://maven.aliyun.com/repository/gradle-plugin" }|g' \
        -e 's|https://dl.google.com/dl/android/maven2|https://maven.aliyun.com/repository/google|g' \
        -e 's|https://maven.google.com|https://maven.aliyun.com/repository/google|g' \
        "$f"
done

find /app/build/darush/android -type f -name AndroidManifest.xml | while read -r manifest; do
    if ! grep -q 'usesCleartextTraffic' "$manifest"; then
        sed -i 's|<application |<application android:usesCleartextTraffic="true" |' "$manifest"
    fi
done

echo "Syncing sources and resources..."
briefcase update android --no-input --update-resources --update-requirements
patch_gradle_properties
patch_pip_options
clean_stale_gradle_locks

find /app/build/darush/android -type f -name AndroidManifest.xml | while read -r manifest; do
    if ! grep -q 'usesCleartextTraffic' "$manifest"; then
        sed -i 's|<application |<application android:usesCleartextTraffic="true" |' "$manifest"
    fi
done

echo "Building debug APK..."
briefcase build android --no-input

APK="/app/build/darush/android/gradle/app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -f "${APK}" ]]; then
    echo "ERROR: APK not found at ${APK}" >&2
    exit 1
fi

cp "${APK}" /output/app-debug.apk
echo "APK copied to /output/app-debug.apk"
