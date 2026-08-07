#!/usr/bin/env bash
set -euo pipefail

readonly BUILDROOT_VERSION="2025.02.16"
readonly BUILDROOT_SHA256="15305e3d366eeaf4a5ecaf2ed42f685fd6af7fe5dbf1f62e1de5f46ee83225e2"
readonly BUILDROOT_URL="https://buildroot.org/downloads/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly LINUX_VERSION="6.12.27"

readonly REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly WORK_DIR="${MICRONUX_M1_WORKDIR:-${HOME}/.cache/micronux/m1}"
readonly DOWNLOAD_DIR="${WORK_DIR}/downloads"
readonly ARCHIVE="${DOWNLOAD_DIR}/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly SOURCE_DIR="${WORK_DIR}/src/buildroot-${BUILDROOT_VERSION}"
readonly EXTERNAL_DIR="${REPO_DIR}/buildroot-external"
readonly DEFCONFIG="micronux_qemu_rv32_nommu_defconfig"
readonly DEFCONFIG_FILE="${EXTERNAL_DIR}/configs/${DEFCONFIG}"
readonly BUSYBOX_FRAGMENT_FILE="${EXTERNAL_DIR}/board/micronux/busybox-m1.config"
readonly JOBS="${MICRONUX_JOBS:-$(nproc)}"

readonly PROFILE_SHA256="$(
    for profile_file in \
        "${DEFCONFIG_FILE}" \
        "${BUSYBOX_FRAGMENT_FILE}"; do
        sha256sum "${profile_file}" | awk '{print $1}'
    done | sha256sum | awk '{print $1}'
)"
readonly CONFIG_ID="${PROFILE_SHA256:0:12}"
readonly OUTPUT_DIR="${WORK_DIR}/output-${BUILDROOT_VERSION}-${CONFIG_ID}"

# Buildroot rejects the Windows paths that WSL appends to PATH. It also builds
# every required cross and host tool, so no host-specific path is needed.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

required_tools=(awk bash bc bison cpio file find flex g++ gcc git gzip make patch perl python3 rsync sed sha256sum tar unzip wget xz)
missing_tools=()
for tool in "${required_tools[@]}"; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        missing_tools+=("${tool}")
    fi
done

if ((${#missing_tools[@]})); then
    printf 'Missing M1 host tools: %s\n' "${missing_tools[*]}" >&2
    printf 'On Ubuntu, install the standard Buildroot prerequisites plus cpio and unzip.\n' >&2
    exit 1
fi

mkdir -p "${DOWNLOAD_DIR}" "${WORK_DIR}/src" "${OUTPUT_DIR}"

if [[ ! -f "${ARCHIVE}" ]]; then
    printf 'Downloading Buildroot %s...\n' "${BUILDROOT_VERSION}"
    wget --no-verbose --output-document="${ARCHIVE}.part" "${BUILDROOT_URL}"
    mv "${ARCHIVE}.part" "${ARCHIVE}"
fi

archive_hash="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${archive_hash}" != "${BUILDROOT_SHA256}" ]]; then
    printf 'Buildroot archive checksum mismatch.\nExpected: %s\nActual:   %s\n' \
        "${BUILDROOT_SHA256}" "${archive_hash}" >&2
    exit 1
fi

if [[ ! -d "${SOURCE_DIR}" ]]; then
    tar --extract --xz --directory="${WORK_DIR}/src" --file="${ARCHIVE}"
fi

if [[ ! -f "${SOURCE_DIR}/Makefile" ]] || ! grep -q "BR2_VERSION := ${BUILDROOT_VERSION}" "${SOURCE_DIR}/Makefile"; then
    printf 'Buildroot source directory is incomplete or has the wrong version: %s\n' "${SOURCE_DIR}" >&2
    exit 1
fi

make -C "${SOURCE_DIR}" \
    O="${OUTPUT_DIR}" \
    BR2_EXTERNAL="${EXTERNAL_DIR}" \
    BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
    "${DEFCONFIG}"

make -C "${SOURCE_DIR}" \
    O="${OUTPUT_DIR}" \
    BR2_EXTERNAL="${EXTERNAL_DIR}" \
    BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
    -j"${JOBS}"

readonly IMAGE_DIR="${OUTPUT_DIR}/images"
readonly FLTHDR="${OUTPUT_DIR}/host/bin/riscv32-buildroot-linux-uclibc-flthdr"
readonly BUSYBOX="${OUTPUT_DIR}/target/bin/busybox"
readonly FLTHDR_OUTPUT="$("${FLTHDR}" -p "${BUSYBOX}")"
readonly BUSYBOX_BSS_END="$(awk '/BSS End:/ {print $3}' <<<"${FLTHDR_OUTPUT}")"
readonly BUSYBOX_STACK_SIZE="$(awk '/Stack Size:/ {print $3}' <<<"${FLTHDR_OUTPUT}")"
readonly BUSYBOX_MEMORY_SPAN="$((BUSYBOX_BSS_END + BUSYBOX_STACK_SIZE))"
(
    cd "${IMAGE_DIR}"
    sha256sum Image rootfs.cpio > SHA256SUMS
)

cat >"${IMAGE_DIR}/VERSIONS" <<EOF
Buildroot ${BUILDROOT_VERSION}
Linux ${LINUX_VERSION}
BusyBox 1.37.0
uClibc-ng 1.0.57
GCC 13.4.0
QEMU 9.2.0
ISA rv32imac_zicsr_zifencei
ABI ilp32
Profile SHA256 ${PROFILE_SHA256}
BusyBox bFLT memory span ${BUSYBOX_MEMORY_SPAN} bytes
EOF

printf 'M1 build complete: %s\n' "${IMAGE_DIR}"
