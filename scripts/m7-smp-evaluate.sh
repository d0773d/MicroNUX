#!/usr/bin/env bash
set -euo pipefail

readonly BUILDROOT_VERSION="2025.02.16"
readonly LINUX_VERSION="6.12.27"
readonly REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly WORK_DIR="${MICRONUX_M7_WORKDIR:-${HOME}/.cache/micronux/m7}"
readonly OUTPUT_DIR="${WORK_DIR}/output-${BUILDROOT_VERSION}"
readonly KERNEL_DIR="${OUTPUT_DIR}/build/linux-${LINUX_VERSION}"
readonly EVAL_ROOT="${WORK_DIR}/smp-eval-${BUILDROOT_VERSION}"
readonly SOURCE_COPY="${EVAL_ROOT}/linux-${LINUX_VERSION}"
readonly BUILD_DIR="${EVAL_ROOT}/build"
readonly ARTIFACT_DIR="${REPO_DIR}/out/m7"
readonly REPORT="${ARTIFACT_DIR}/smp-evaluation.txt"
readonly CROSS_COMPILE="${OUTPUT_DIR}/host/bin/riscv32-buildroot-linux-uclibc-"
readonly PARTITION_SIZE=$((0x600000))
readonly JOBS="${MICRONUX_JOBS:-$(nproc)}"

for required in \
	"${KERNEL_DIR}/.config" \
	"${KERNEL_DIR}/arch/riscv/boot/Image" \
	"${KERNEL_DIR}/vmlinux" \
	"${OUTPUT_DIR}/images/rootfs.cpio" \
	"${CROSS_COMPILE}gcc"; do
	if [[ ! -e "${required}" ]]; then
		printf 'Missing M7 baseline artifact: %s\nRun scripts/m7-build.sh first.\n' \
			"${required}" >&2
		exit 1
	fi
done

case "${EVAL_ROOT}" in
	"${WORK_DIR}"/smp-eval-*) ;;
	*)
		printf 'Refusing unsafe SMP evaluation path: %s\n' "${EVAL_ROOT}" >&2
		exit 1
		;;
esac

# Keep the accepted M7 source/build tree immutable.  The generated Buildroot
# Linux directory is copied, cleaned, and rebuilt in a task-specific cache.
rm -rf -- "${EVAL_ROOT}"
mkdir -p "${EVAL_ROOT}" "${ARTIFACT_DIR}"
cp -a "${KERNEL_DIR}" "${SOURCE_COPY}"
make -C "${SOURCE_COPY}" ARCH=riscv mrproper
# Buildroot leaves this generated directory in some incremental trees even
# after mrproper; an out-of-tree Linux build correctly rejects it.
rm -rf -- "${SOURCE_COPY}/arch/riscv/include/generated"

mkdir -p "${BUILD_DIR}"
cp "${KERNEL_DIR}/.config" "${BUILD_DIR}/.config"
"${SOURCE_COPY}/scripts/config" --file "${BUILD_DIR}/.config" \
	-d MICRONUX_ESP32P4_ISOLATION \
	-e SMP \
	--set-val NR_CPUS 2 \
	-e RISCV_BOOT_SPINWAIT

export ARCH=riscv
export BR_BINARIES_DIR="${OUTPUT_DIR}/images"
export CROSS_COMPILE

make -C "${SOURCE_COPY}" O="${BUILD_DIR}" olddefconfig
grep -qx 'CONFIG_SMP=y' "${BUILD_DIR}/.config"
grep -qx 'CONFIG_NR_CPUS=2' "${BUILD_DIR}/.config"
grep -qx 'CONFIG_RISCV_BOOT_SPINWAIT=y' "${BUILD_DIR}/.config"
if grep -qx 'CONFIG_MICRONUX_ESP32P4_ISOLATION=y' "${BUILD_DIR}/.config"; then
	printf 'SMP evaluation unexpectedly retained production isolation.\n' >&2
	exit 1
fi

make -C "${SOURCE_COPY}" O="${BUILD_DIR}" -j"${JOBS}" Image vmlinux

"${CROSS_COMPILE}nm" "${BUILD_DIR}/vmlinux" > "${BUILD_DIR}/System.map.audit"
for symbol in secondary_start_sbi smp_callin spinwait_cpu_start clint_send_ipi; do
	if ! grep -qw "${symbol}" "${BUILD_DIR}/System.map.audit"; then
		printf 'SMP evaluation kernel is missing symbol: %s\n' "${symbol}" >&2
		exit 1
	fi
done

baseline_image_size="$(stat --format='%s' "${KERNEL_DIR}/arch/riscv/boot/Image")"
smp_image_size="$(stat --format='%s' "${BUILD_DIR}/arch/riscv/boot/Image")"
baseline_vmlinux_size="$(stat --format='%s' "${KERNEL_DIR}/vmlinux")"
smp_vmlinux_size="$(stat --format='%s' "${BUILD_DIR}/vmlinux")"
image_delta=$((smp_image_size - baseline_image_size))
vmlinux_delta=$((smp_vmlinux_size - baseline_vmlinux_size))
partition_delta=$((smp_image_size - PARTITION_SIZE))
image_delta_percent="$(awk -v delta="${image_delta}" -v base="${baseline_image_size}" \
	'BEGIN { printf "%.3f", (delta * 100.0) / base }')"

if ((partition_delta > 0)); then
	partition_result="over-by-${partition_delta}"
else
	partition_result="free-$((-partition_delta))"
fi

{
	printf 'MICRONUX:M7:SMP-EVALUATION state=compiled decision=defer\n'
	printf 'linux=%s buildroot=%s\n' "${LINUX_VERSION}" "${BUILDROOT_VERSION}"
	printf 'config_smp=y nr_cpus=2 boot=spinwait isolation=n\n'
	printf 'baseline_image_bytes=%s\n' "${baseline_image_size}"
	printf 'smp_image_bytes=%s\n' "${smp_image_size}"
	printf 'image_delta_bytes=%s\n' "${image_delta}"
	printf 'image_delta_percent=%s\n' "${image_delta_percent}"
	printf 'linux_partition_bytes=%s\n' "${PARTITION_SIZE}"
	printf 'partition_result=%s\n' "${partition_result}"
	printf 'baseline_vmlinux_bytes=%s\n' "${baseline_vmlinux_size}"
	printf 'smp_vmlinux_bytes=%s\n' "${smp_vmlinux_size}"
	printf 'vmlinux_delta_bytes=%s\n' "${vmlinux_delta}"
	printf 'baseline_image_sha256=%s\n' \
		"$(sha256sum "${KERNEL_DIR}/arch/riscv/boot/Image" | awk '{print $1}')"
	printf 'smp_image_sha256=%s\n' \
		"$(sha256sum "${BUILD_DIR}/arch/riscv/boot/Image" | awk '{print $1}')"
	printf 'smp_config_sha256=%s\n' \
		"$(sha256sum "${BUILD_DIR}/.config" | awk '{print $1}')"
	"${CROSS_COMPILE}size" "${KERNEL_DIR}/vmlinux" "${BUILD_DIR}/vmlinux"
} | tee "${REPORT}"

printf 'SMP evaluation complete: %s\n' "${REPORT}"
