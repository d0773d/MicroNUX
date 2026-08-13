#!/usr/bin/env bash
set -euo pipefail

readonly BUILDROOT_VERSION="2025.02.16"
readonly BUILDROOT_SHA256="15305e3d366eeaf4a5ecaf2ed42f685fd6af7fe5dbf1f62e1de5f46ee83225e2"
readonly BUILDROOT_URL="https://buildroot.org/downloads/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly LINUX_VERSION="6.12.27"

readonly REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly WORK_DIR="${MICRONUX_M5_WORKDIR:-${HOME}/.cache/micronux/m5}"
readonly DOWNLOAD_DIR="${MICRONUX_DOWNLOAD_DIR:-${HOME}/.cache/micronux/m1/downloads}"
readonly ARCHIVE="${DOWNLOAD_DIR}/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly SOURCE_DIR="${WORK_DIR}/src/buildroot-${BUILDROOT_VERSION}"
readonly EXTERNAL_DIR="${REPO_DIR}/buildroot-external"
readonly DEFCONFIG="micronux_esp32p4_hardening_defconfig"
readonly JOBS="${MICRONUX_JOBS:-$(nproc)}"
readonly OUTPUT_DIR="${WORK_DIR}/output-${BUILDROOT_VERSION}"
readonly ARTIFACT_DIR="${REPO_DIR}/out/m5"

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

required_tools=(awk bash bc bison cpio file find flex g++ gcc git gzip make patch perl python3 rsync sed sha256sum tar unzip wget xz)
missing_tools=()
for tool in "${required_tools[@]}"; do
	if ! command -v "${tool}" >/dev/null 2>&1; then
		missing_tools+=("${tool}")
	fi
done
if ((${#missing_tools[@]})); then
	printf 'Missing M5 host tools: %s\n' "${missing_tools[*]}" >&2
	exit 1
fi

mkdir -p "${DOWNLOAD_DIR}" "${WORK_DIR}/src" "${OUTPUT_DIR}" "${ARTIFACT_DIR}"
if [[ ! -f "${ARCHIVE}" ]]; then
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
if [[ ! -f "${SOURCE_DIR}/Makefile" ]] ||
	! grep -q "BR2_VERSION := ${BUILDROOT_VERSION}" "${SOURCE_DIR}/Makefile"; then
	printf 'Buildroot source directory is incomplete: %s\n' "${SOURCE_DIR}" >&2
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
readonly DTB="${IMAGE_DIR}/esp32p4-micronux.dtb"
python3 "${REPO_DIR}/scripts/m3-pack.py" \
	--image "${IMAGE_DIR}/Image" \
	--dtb "${DTB}" \
	--output "${ARTIFACT_DIR}/metadata.bin"

install -m 0644 "${IMAGE_DIR}/Image" "${ARTIFACT_DIR}/Image"
install -m 0644 "${DTB}" "${ARTIFACT_DIR}/esp32p4-micronux.dtb"
install -m 0644 "${IMAGE_DIR}/rootfs.cpio" "${ARTIFACT_DIR}/rootfs.cpio"
install -m 0644 "${OUTPUT_DIR}/build/linux-${LINUX_VERSION}/.config" "${ARTIFACT_DIR}/linux.config"
install -m 0644 "${OUTPUT_DIR}/build/linux-${LINUX_VERSION}/vmlinux" "${ARTIFACT_DIR}/vmlinux"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-selftest" \
	"${ARTIFACT_DIR}/micronux-selftest"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-exec-child" \
	"${ARTIFACT_DIR}/micronux-exec-child"
(
	cd "${ARTIFACT_DIR}"
	sha256sum Image esp32p4-micronux.dtb metadata.bin rootfs.cpio \
		micronux-selftest micronux-exec-child > SHA256SUMS
)

printf 'M5 build complete: %s\n' "${ARTIFACT_DIR}"
