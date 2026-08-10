/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "gc.h"
#include "ignite_vm.h"
#include "micronux/device.h"
#include "native_hardware.h"
#include "package_loader.h"

#define MICRONUX_IGNITE_PACKAGE_MAX (256u * 1024u)
#define MICRONUX_IGNITE_RUN_LIMIT 4096u
#define MICRONUX_IGNITE_BUDGET 4096u
#define MICRONUX_IGNITE_UID 65534u
#define MICRONUX_IGNITE_GID 65534u

enum ignite_network_status {
	IGNITE_NETWORK_UNKNOWN = 0,
	IGNITE_NETWORK_CONNECTING = 3,
	IGNITE_NETWORK_CONNECTED = 4,
	IGNITE_NETWORK_DISABLED = 6,
};

struct micronux_ignite_host {
	struct micronux_device_info info;
	uint32_t devices;
	int32_t network_status;
};

struct micronux_ignite_runtime {
	IgniteVm vm;
	IgniteGc gc;
	IgniteVmFiberState state;
	NativeHardwareContext native;
	IgniteFiber fiber;
};

static int read_package(const char *path, uint8_t **data_out, size_t *size_out)
{
	struct stat status;
	uint8_t *data;
	size_t offset = 0;
	int descriptor;

	if (path == NULL || data_out == NULL || size_out == NULL) {
		errno = EINVAL;
		return -1;
	}
	descriptor = open(path, O_RDONLY);
	if (descriptor < 0)
		return -1;
	if (fstat(descriptor, &status) < 0 || status.st_size <= 0 ||
		(uint64_t)status.st_size > MICRONUX_IGNITE_PACKAGE_MAX) {
		int saved_errno = errno == 0 ? EFBIG : errno;

		close(descriptor);
		errno = saved_errno;
		return -1;
	}
	data = malloc((size_t)status.st_size);
	if (data == NULL) {
		close(descriptor);
		return -1;
	}
	while (offset < (size_t)status.st_size) {
		ssize_t count = read(descriptor, data + offset,
			(size_t)status.st_size - offset);

		if (count < 0 && errno == EINTR)
			continue;
		if (count <= 0) {
			int saved_errno = count == 0 ? EIO : errno;

			free(data);
			close(descriptor);
			errno = saved_errno;
			return -1;
		}
		offset += (size_t)count;
	}
	if (close(descriptor) < 0) {
		free(data);
		return -1;
	}
	*data_out = data;
	*size_out = offset;
	return 0;
}

static int drop_vm_privileges(void)
{
	if (geteuid() != 0)
		return 0;
	if (setgid((gid_t)MICRONUX_IGNITE_GID) < 0 ||
		setuid((uid_t)MICRONUX_IGNITE_UID) < 0)
		return -1;
	return 0;
}

static int micronux_network_status(int32_t *status_out, void *user)
{
	struct micronux_ignite_host *host = user;

	if (status_out == NULL || host == NULL)
		return -1;
	if (micronux_device_get_info(&host->info) < 0 ||
		micronux_device_list(&host->devices) < 0)
		return -1;
	if ((host->devices & MICRONUX_DEVICE_NETWORK_UP) != 0)
		host->network_status = IGNITE_NETWORK_CONNECTED;
	else if ((host->devices & MICRONUX_DEVICE_NETWORK_PRESENT) != 0)
		host->network_status = IGNITE_NETWORK_CONNECTING;
	else
		host->network_status = IGNITE_NETWORK_DISABLED;
	*status_out = host->network_status;
	return 0;
}

static int run_package(const IgpkView *view,
	struct micronux_ignite_host *host)
{
	struct micronux_ignite_runtime *runtime;
	VmRunResult result = { VM_RUN_FAULT, 0, 0 };
	unsigned int runs;

	runtime = calloc(1, sizeof(*runtime));
	if (runtime == NULL)
		return -1;
	ignite_vm_init(&runtime->vm);
	gc_init(&runtime->gc);
	ignite_vm_attach_gc(&runtime->vm, &runtime->gc);
	native_hardware_init(&runtime->native, view->permissions,
		NULL, NULL, NULL, NULL);
	ignite_vm_set_network_status(&runtime->vm,
		micronux_network_status, host);
	ignite_vm_fiber_state_init(&runtime->state, view->bytecode,
		view->header.bytecode_length, &runtime->native);
	runtime->fiber.id = 1;
	runtime->fiber.state = FIBER_READY;
	runtime->fiber.vm_state = &runtime->state;

	for (runs = 0; runs < MICRONUX_IGNITE_RUN_LIMIT; runs++) {
		result = ignite_vm_resume_fiber(ignite_vm_as_context(&runtime->vm),
			&runtime->fiber, MICRONUX_IGNITE_BUDGET);
		if (result.status == VM_RUN_DONE || result.status == VM_RUN_FAULT)
			break;
		if (result.status != VM_RUN_OK && result.status != VM_RUN_YIELDED) {
			fprintf(stderr,
				"MICRONUX:M8:IGNITE:FAIL unsupported-run-state=%d\n",
				(int)result.status);
			gc_deinit(&runtime->gc);
			free(runtime);
			errno = ENOTSUP;
			return -1;
		}
	}
	if (result.status == VM_RUN_FAULT) {
		fprintf(stderr,
			"MICRONUX:M8:IGNITE:FAULT fault=%s native_status=%d\n",
			ignite_vm_fault_string(runtime->state.fault),
			(int)runtime->state.last_native_status);
		gc_deinit(&runtime->gc);
		free(runtime);
		errno = ECANCELED;
		return -1;
	}
	if (result.status != VM_RUN_DONE) {
		fprintf(stderr, "MICRONUX:M8:IGNITE:FAIL run-limit\n");
		gc_deinit(&runtime->gc);
		free(runtime);
		errno = ETIMEDOUT;
		return -1;
	}
	if (runtime->state.globals[0] != host->network_status) {
		fprintf(stderr,
			"MICRONUX:M8:IGNITE:FAIL result-mismatch global0=%" PRId32
			" callback=%" PRId32 "\n",
			runtime->state.globals[0], host->network_status);
		gc_deinit(&runtime->gc);
		free(runtime);
		errno = EPROTO;
		return -1;
	}
	printf("MICRONUX:M8:IGNITE:PASS abi=%u.%u uid=%lu "
		"capabilities=0x%08" PRIx32 " devices=0x%08" PRIx32
		" network_status=%" PRId32 " global0=%" PRId32 "\n",
		host->info.abi_major, host->info.abi_minor,
		(unsigned long)geteuid(), host->info.capabilities, host->devices,
		host->network_status, runtime->state.globals[0]);
	gc_deinit(&runtime->gc);
	free(runtime);
	return 0;
}

int main(int argc, char **argv)
{
	struct micronux_ignite_host host = {
		.network_status = IGNITE_NETWORK_UNKNOWN,
	};
	const char *path = "/usr/share/micronux/ignite/device-status.igpk";
	uint8_t *package = NULL;
	size_t package_size = 0;
	IgpkView view;
	IgpkStatus status;
	int result;

	if (argc == 2)
		path = argv[1];
	else if (argc != 1) {
		fprintf(stderr, "usage: micronux-ignite [package.igpk]\n");
		return 2;
	}
	if (read_package(path, &package, &package_size) < 0) {
		perror("micronux-ignite: read package");
		return 1;
	}
	status = igpk_validate_view(package, package_size, &view);
	if (status != IGPK_OK) {
		fprintf(stderr, "micronux-ignite: invalid package: %s\n",
			igpk_status_string(status));
		free(package);
		return 1;
	}
	if (drop_vm_privileges() < 0) {
		perror("micronux-ignite: drop privileges");
		free(package);
		return 1;
	}
	result = run_package(&view, &host);
	free(package);
	return result == 0 ? 0 : 1;
}
