#!/bin/bash
# Run inside Docker: sync sources and build signed release APK/AAB artifacts.
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

ANDROID_DIR="/app/build/options/android"
GRADLE_DIR="${ANDROID_DIR}/gradle"

require_release_env() {
    local missing=0
    for name in RELEASE_KEYSTORE RELEASE_KEY_ALIAS RELEASE_STORE_PASSWORD RELEASE_KEY_PASSWORD; do
        if [[ -z "${!name:-}" ]]; then
            echo "ERROR: ${name} is required for release signing." >&2
            missing=1
        fi
    done
    if [[ "${missing}" == "1" ]]; then
        exit 1
    fi
    if [[ ! -f "${RELEASE_KEYSTORE}" ]]; then
        echo "ERROR: release keystore not found at ${RELEASE_KEYSTORE}" >&2
        exit 1
    fi
}

stop_gradle_daemons() {
    local gradle_dir="${GRADLE_DIR}"
    if [[ -x "${gradle_dir}/gradlew" ]]; then
        (cd "${gradle_dir}" && ./gradlew --stop >/dev/null 2>&1) || true
    fi
    pkill -f 'GradleDaemon' >/dev/null 2>&1 || true
}

clean_stale_gradle_locks() {
    stop_gradle_daemons
    rm -rf "${GRADLE_DIR}/.gradle" 2>/dev/null || true
    if [[ -d "${GRADLE_USER_HOME}/caches" ]]; then
        find "${GRADLE_USER_HOME}/caches" -type f -name "*.lock" -delete 2>/dev/null || true
    fi
}

patch_gradle_properties() {
    local props="${GRADLE_DIR}/gradle.properties"
    [[ -f "${props}" ]] || return 0
    grep -q '^org\.gradle\.daemon=false$' "${props}" || printf '\norg.gradle.daemon=false\n' >> "${props}"
    grep -q '^org\.gradle\.vfs\.watch=false$' "${props}" || printf 'org.gradle.vfs.watch=false\n' >> "${props}"
    grep -q '^org\.gradle\.parallel=false$' "${props}" || printf 'org.gradle.parallel=false\n' >> "${props}"
}

patch_pip_options() {
    local opts="${GRADLE_DIR}/app/pip-options.txt"
    local tmp
    [[ -f "${opts}" ]] || return 0
    tmp="$(mktemp)"
    {
        printf '%s\n' \
            "--index-url" \
            "https://mirrors.aliyun.com/pypi/simple/" \
            "--extra-index-url" \
            "https://chaquo.com/pypi-13.1"
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

harden_android_manifest() {
    local xml_dir="${GRADLE_DIR}/app/src/main/res/xml"
    local network_config="${xml_dir}/network_security_config.xml"
    mkdir -p "${xml_dir}"
    cat > "${network_config}" <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="false" />
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="false">127.0.0.1</domain>
        <domain includeSubdomains="false">localhost</domain>
    </domain-config>
</network-security-config>
EOF

    find "${ANDROID_DIR}" -type f -name AndroidManifest.xml | while read -r manifest; do
        sed -i \
            -e 's/android:allowBackup="true"/android:allowBackup="false"/g' \
            -e 's/android:usesCleartextTraffic="true"//g' \
            "${manifest}"
        if ! grep -q 'android:networkSecurityConfig=' "${manifest}"; then
            sed -i 's|<application |<application android:networkSecurityConfig="@xml/network_security_config" |' "${manifest}"
        fi
    done
}

patch_android_theme() {
    find "${GRADLE_DIR}/app/src/main/res" -type f -name "styles.xml" | while read -r styles; do
        sed -i \
            -e 's|Theme\.AppCompat\.Light\.DarkActionBar|Theme.AppCompat.Light.NoActionBar|g' \
            -e 's|Theme\.AppCompat\.Light\.ActionBar|Theme.AppCompat.Light.NoActionBar|g' \
            "${styles}"
        if ! grep -q 'name="windowNoTitle"' "${styles}"; then
            sed -i '/<item name="colorAccent">/a\        <item name="windowNoTitle">true</item>' "${styles}"
            sed -i '/<item name="windowNoTitle">true<\/item>/a\        <item name="windowActionBar">false</item>' "${styles}"
            sed -i '/<item name="windowActionBar">false<\/item>/a\        <item name="android:windowNoTitle">true</item>' "${styles}"
            sed -i '/<item name="android:windowNoTitle">true<\/item>/a\        <item name="android:windowActionBar">false</item>' "${styles}"
        fi
    done
}

patch_release_signing() {
    local gradle_file="${GRADLE_DIR}/app/build.gradle"
    [[ -f "${gradle_file}" ]] || return 0
    python3 - "${gradle_file}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

signing = """    signingConfigs {
        release {
            storeFile file(System.getenv("RELEASE_KEYSTORE"))
            storePassword System.getenv("RELEASE_STORE_PASSWORD")
            keyAlias System.getenv("RELEASE_KEY_ALIAS")
            keyPassword System.getenv("RELEASE_KEY_PASSWORD")
        }
    }

"""

if "signingConfigs {" not in text:
    text = text.replace("    buildTypes {\n", signing + "    buildTypes {\n", 1)

if "signingConfig signingConfigs.release" not in text:
    text = text.replace(
        "        release {\n            minifyEnabled false\n",
        "        release {\n            signingConfig signingConfigs.release\n            minifyEnabled false\n",
        1,
    )

path.write_text(text, encoding="utf-8")
PY
}

require_release_env

if [[ ! -f "${GRADLE_DIR}/briefcase.toml" ]]; then
    if [[ -d "${ANDROID_DIR}" ]]; then
        echo "Android Gradle project is incomplete; recreating it..."
        rm -rf "${ANDROID_DIR}"
    fi
    echo "Creating Android Gradle project..."
    briefcase create android --no-input
fi

clean_stale_gradle_locks

rm -rf "${GRADLE_DIR}/app/src/main/res/values-v35"

find /app/build -type f -name "*.gradle" | while read -r f; do
    sed -i -E \
        -e 's|google\(\)|maven { url "https://maven.aliyun.com/repository/google" }|g' \
        -e 's|mavenCentral\(\)|maven { url "https://maven.aliyun.com/repository/central" }|g' \
        -e 's|gradlePluginPortal\(\)|maven { url "https://maven.aliyun.com/repository/gradle-plugin" }|g' \
        -e 's|https://dl.google.com/dl/android/maven2|https://maven.aliyun.com/repository/google|g' \
        -e 's|https://maven.google.com|https://maven.aliyun.com/repository/google|g' \
        "$f"
done

harden_android_manifest
patch_android_theme

echo "Syncing sources and resources..."
briefcase update android --no-input --update-resources --update-requirements
patch_gradle_properties
patch_pip_options
clean_stale_gradle_locks

harden_android_manifest
patch_android_theme
patch_release_signing

echo "Building signed release APK..."
(cd "${GRADLE_DIR}" && ./gradlew --no-daemon assembleRelease)

echo "Building signed release AAB..."
(cd "${GRADLE_DIR}" && ./gradlew --no-daemon bundleRelease)

APK="${GRADLE_DIR}/app/build/outputs/apk/release/app-release.apk"
AAB="${GRADLE_DIR}/app/build/outputs/bundle/release/app-release.aab"
if [[ ! -f "${APK}" ]]; then
    echo "ERROR: release APK not found at ${APK}" >&2
    exit 1
fi

cp "${APK}" /output/tiger-options-release.apk
if [[ -f "${AAB}" ]]; then
    cp "${AAB}" /output/tiger-options-release.aab
fi
echo "Release APK copied to /output/tiger-options-release.apk"
if [[ -f /output/tiger-options-release.aab ]]; then
    echo "Release AAB copied to /output/tiger-options-release.aab"
fi
