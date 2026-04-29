#!/usr/bin/env bash
#
# Build, sign, notarize, and package "MacroFlow" for distribution.
#
# Output: dist/MacroFlow.dmg
#
# Usage:  ./build_and_sign.sh
#

set -euo pipefail

# ----- Config ---------------------------------------------------------------

APP_NAME="MacroFlow"
APP_PATH="dist/${APP_NAME}.app"
DMG_NAME="MacroFlow"
DMG_PATH="dist/${DMG_NAME}.dmg"
ENTITLEMENTS="entitlements.plist"
NOTARY_PROFILE="${NOTARY_PROFILE:-chads-davinci-notary}"

# Auto-detect signing identity
if [[ -z "${SIGN_IDENTITY:-}" ]]; then
    SIGN_IDENTITY="$(security find-identity -v -p codesigning \
        | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
fi
if [[ -z "${SIGN_IDENTITY:-}" ]]; then
    echo "ERROR: No 'Developer ID Application' code-signing identity found."
    exit 1
fi

echo "==> Using signing identity: ${SIGN_IDENTITY}"
echo "==> Using notarytool profile: ${NOTARY_PROFILE}"

# ----- 1. Clean + build .app via py2app ------------------------------------

echo "==> Cleaning previous build..."
rm -rf build dist

PYPROJECT_HIDDEN=0
if [[ -f pyproject.toml ]]; then
    mv pyproject.toml pyproject.toml.bak
    PYPROJECT_HIDDEN=1
fi
trap '[[ "${PYPROJECT_HIDDEN}" == "1" ]] && [[ -f pyproject.toml.bak ]] && mv pyproject.toml.bak pyproject.toml || true' EXIT

echo "==> Building .app via py2app..."
PYTHONPATH=src python3 setup.py py2app

if [[ ! -d "${APP_PATH}" ]]; then
    echo "ERROR: py2app did not produce ${APP_PATH}"
    exit 1
fi

if [[ "${PYPROJECT_HIDDEN}" == "1" ]] && [[ -f pyproject.toml.bak ]]; then
    mv pyproject.toml.bak pyproject.toml
    PYPROJECT_HIDDEN=0
fi

# ----- 2. Sign nested binaries ---------------------------------------------

echo "==> Signing nested Mach-O binaries..."
find "${APP_PATH}" \
    \( -name "*.dylib" -o -name "*.so" \
       -o -path "*/Contents/MacOS/*" -o -path "*/Frameworks/*/Versions/*/Python*" \) \
    -type f -print0 \
  | while IFS= read -r -d '' f; do
        codesign --force --options runtime --timestamp \
                 --entitlements "${ENTITLEMENTS}" \
                 --sign "${SIGN_IDENTITY}" "$f" || true
    done

# ----- 3. Sign the .app bundle ---------------------------------------------

echo "==> Signing the app bundle..."
codesign --force --deep --options runtime --timestamp \
         --entitlements "${ENTITLEMENTS}" \
         --sign "${SIGN_IDENTITY}" "${APP_PATH}"

echo "==> Verifying signature..."
codesign --verify --deep --strict --verbose=2 "${APP_PATH}"

# ----- 4. Notarize ---------------------------------------------------------

ZIP_PATH="dist/${APP_NAME}.zip"
echo "==> Zipping for notarization..."
ditto -c -k --sequesterRsrc --keepParent "${APP_PATH}" "${ZIP_PATH}"

echo "==> Submitting to Apple notarization service..."
xcrun notarytool submit "${ZIP_PATH}" \
    --keychain-profile "${NOTARY_PROFILE}" \
    --wait

echo "==> Stapling notarization ticket..."
xcrun stapler staple "${APP_PATH}"
xcrun stapler validate "${APP_PATH}"

if [[ ! -f "${APP_PATH}/Contents/CodeResources" ]]; then
    echo "ERROR: ${APP_PATH}/Contents/CodeResources missing after staple."
    exit 1
fi

rm -f "${ZIP_PATH}"

# ----- 5. Build the DMG ----------------------------------------------------

echo "==> Building DMG via dmgbuild..."
rm -f "${DMG_PATH}"

if python3 -c "import dmgbuild" 2>/dev/null; then
    python3 -m dmgbuild -s dmg_settings.py "MacroFlow" "${DMG_PATH}"
elif command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "${DMG_NAME}" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "${APP_NAME}.app" 150 190 \
        --app-drop-link 450 190 \
        "${DMG_PATH}" \
        "${APP_PATH}"
else
    hdiutil create -volname "${DMG_NAME}" -srcfolder "${APP_PATH}" \
        -ov -format UDZO "${DMG_PATH}"
fi

echo "==> Verifying .app inside DMG is stapled..."
_VERIFY_MNT="$(mktemp -d)/mf_verify"
mkdir -p "${_VERIFY_MNT}"
hdiutil attach "${DMG_PATH}" -nobrowse -mountpoint "${_VERIFY_MNT}" >/dev/null
if ! xcrun stapler validate "${_VERIFY_MNT}/${APP_NAME}.app" >/dev/null 2>&1; then
    hdiutil detach "${_VERIFY_MNT}" >/dev/null || true
    echo "ERROR: .app inside DMG is NOT stapled. Aborting before DMG signing."
    exit 1
fi
hdiutil detach "${_VERIFY_MNT}" >/dev/null

DMG_SIZE_BYTES=$(stat -f %z "${DMG_PATH}")
DMG_SIZE_MB=$((DMG_SIZE_BYTES / 1048576))
DMG_BUDGET_MB=30
echo "==> DMG size: ${DMG_SIZE_MB} MB (budget ${DMG_BUDGET_MB} MB)"
if [ "${DMG_SIZE_MB}" -gt "${DMG_BUDGET_MB}" ]; then
    echo "ERROR: DMG is ${DMG_SIZE_MB} MB, exceeds ${DMG_BUDGET_MB} MB budget."
    exit 1
fi

# ----- 6. Sign + notarize the DMG ------------------------------------------

echo "==> Signing the DMG..."
codesign --force --sign "${SIGN_IDENTITY}" --timestamp "${DMG_PATH}"

echo "==> Notarizing the DMG..."
xcrun notarytool submit "${DMG_PATH}" \
    --keychain-profile "${NOTARY_PROFILE}" \
    --wait

echo "==> Stapling DMG..."
xcrun stapler staple "${DMG_PATH}"
xcrun stapler validate "${DMG_PATH}"

echo "==> Generating SHA-256 checksum..."
SHA_PATH="${DMG_PATH}.sha256"
( cd "$(dirname "${DMG_PATH}")" && shasum -a 256 "$(basename "${DMG_PATH}")" ) > "${SHA_PATH}"
echo "==> Wrote ${SHA_PATH}"
cat "${SHA_PATH}"

echo
echo "================================================================="
echo " SUCCESS"
echo " Signed + notarized DMG: ${DMG_PATH}"
echo " SHA-256 checksum:       ${SHA_PATH}"
echo "================================================================="
