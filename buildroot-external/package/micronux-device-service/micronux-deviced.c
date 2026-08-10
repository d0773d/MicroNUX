/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "device-protocol.h"

#define CLIENT_LIMIT 8
#define SERVICE_POLL_MS 100
#define SERVICE_PID_PATH "/run/micronux/deviced.pid"
#define DEVICE_FLAG_MASK (MICRONUX_DEVICE_STORAGE_READY | \
	MICRONUX_DEVICE_NETWORK_PRESENT | MICRONUX_DEVICE_NETWORK_UP)

enum client_state {
	CLIENT_FREE,
	CLIENT_REQUEST,
	CLIENT_WAIT,
};

struct service_client {
	int fd;
	enum client_state state;
	uid_t uid;
	uint32_t capabilities;
	uint32_t wait_mask;
	int64_t deadline_ms;
	struct micronux_device_request request;
};

static volatile sig_atomic_t stop_requested;

static void request_stop(int signal_number)
{
	(void)signal_number;
	stop_requested = 1;
}

static int64_t monotonic_ms(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static uint32_t peer_capabilities(uid_t uid)
{
	if (uid == 0)
		return MICRONUX_DEVICE_CAP_OBSERVE |
			MICRONUX_DEVICE_CAP_CONTROL |
			MICRONUX_DEVICE_CAP_ADMIN;
	return MICRONUX_DEVICE_CAP_OBSERVE;
}

static uint32_t required_capability(uint32_t operation)
{
	switch (operation) {
	case MICRONUX_DEVICE_OP_INFO:
		return 0;
	case MICRONUX_DEVICE_OP_LIST:
	case MICRONUX_DEVICE_OP_WAIT:
		return MICRONUX_DEVICE_CAP_OBSERVE;
	case MICRONUX_DEVICE_OP_ADMIN_PROBE:
		return MICRONUX_DEVICE_CAP_ADMIN;
	default:
		return UINT32_MAX;
	}
}

static bool file_contains(const char *path, const char *value)
{
	char buffer[32];
	ssize_t length;
	int fd = open(path, O_RDONLY | O_CLOEXEC);

	if (fd < 0)
		return false;
	length = read(fd, buffer, sizeof(buffer) - 1);
	close(fd);
	if (length <= 0)
		return false;
	buffer[length] = '\0';
	return strncmp(buffer, value, strlen(value)) == 0;
}

static uint32_t current_devices(void)
{
	struct stat status;
	uint32_t devices = 0;

	if (stat("/dev/mmcblk0", &status) == 0 && S_ISBLK(status.st_mode))
		devices |= MICRONUX_DEVICE_STORAGE_READY;
	if (stat("/sys/class/net/ethsta0", &status) == 0 &&
		S_ISDIR(status.st_mode)) {
		devices |= MICRONUX_DEVICE_NETWORK_PRESENT;
		if (file_contains("/sys/class/net/ethsta0/operstate", "up"))
			devices |= MICRONUX_DEVICE_NETWORK_UP;
	}
	return devices;
}

static void close_client(struct service_client *client)
{
	if (client->fd >= 0)
		close(client->fd);
	memset(client, 0, sizeof(*client));
	client->fd = -1;
	client->state = CLIENT_FREE;
}

static void respond(struct service_client *client, int32_t status,
		uint32_t devices)
{
	const struct micronux_device_response response = {
		.magic = MICRONUX_DEVICE_MAGIC,
		.abi_major = MICRONUX_DEVICE_ABI_MAJOR,
		.abi_minor = MICRONUX_DEVICE_ABI_MINOR,
		.request_id = client->request.request_id,
		.status = status,
		.capabilities = client->capabilities,
		.devices = devices,
		.value = 0,
	};
	ssize_t length = send(client->fd, &response, sizeof(response),
		MSG_DONTWAIT | MSG_NOSIGNAL);

	if (length != (ssize_t)sizeof(response) &&
		errno != EPIPE && errno != ECONNRESET)
		perror("micronux-deviced: send");
	close_client(client);
}

static void process_request(struct service_client *client)
{
	uint32_t required;
	uint32_t devices = current_devices();
	int64_t now_ms;

	if (client->request.magic != MICRONUX_DEVICE_MAGIC ||
		client->request.abi_major != MICRONUX_DEVICE_ABI_MAJOR) {
		respond(client, MICRONUX_DEVICE_STATUS_VERSION, devices);
		return;
	}
	required = required_capability(client->request.operation);
	if (required == UINT32_MAX) {
		respond(client, MICRONUX_DEVICE_STATUS_INVALID, devices);
		return;
	}
	if ((client->capabilities & required) != required) {
		printf("MICRONUX:M8:AUDIT uid=%lu op=%lu decision=deny\n",
			(unsigned long)client->uid,
			(unsigned long)client->request.operation);
		respond(client, MICRONUX_DEVICE_STATUS_DENIED, devices);
		return;
	}

	switch (client->request.operation) {
	case MICRONUX_DEVICE_OP_INFO:
	case MICRONUX_DEVICE_OP_LIST:
	case MICRONUX_DEVICE_OP_ADMIN_PROBE:
		respond(client, MICRONUX_DEVICE_STATUS_OK, devices);
		return;
	case MICRONUX_DEVICE_OP_WAIT:
		if (client->request.value == 0 ||
			(client->request.value & ~DEVICE_FLAG_MASK) != 0 ||
			client->request.timeout_ms == 0 ||
			client->request.timeout_ms > MICRONUX_DEVICE_MAX_WAIT_MS) {
			respond(client, MICRONUX_DEVICE_STATUS_INVALID, devices);
			return;
		}
		if ((devices & client->request.value) == client->request.value) {
			respond(client, MICRONUX_DEVICE_STATUS_OK, devices);
			return;
		}
		now_ms = monotonic_ms();
		if (now_ms < 0) {
			respond(client, MICRONUX_DEVICE_STATUS_IO, devices);
			return;
		}
		client->wait_mask = client->request.value;
		client->deadline_ms = now_ms + client->request.timeout_ms;
		client->state = CLIENT_WAIT;
		return;
	default:
		respond(client, MICRONUX_DEVICE_STATUS_INVALID, devices);
	}
}

static void receive_request(struct service_client *client)
{
	ssize_t length = recv(client->fd, &client->request,
		sizeof(client->request), MSG_DONTWAIT);

	if (length == (ssize_t)sizeof(client->request)) {
		process_request(client);
		return;
	}
	if (length < 0 && (errno == EAGAIN || errno == EWOULDBLOCK ||
		errno == EINTR))
		return;
	if (length >= 0)
		respond(client, MICRONUX_DEVICE_STATUS_INVALID, current_devices());
	else
		close_client(client);
}

static int set_nonblocking(int fd)
{
	int flags = fcntl(fd, F_GETFL, 0);

	if (flags < 0)
		return -1;
	return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static void accept_client(int listen_fd, struct service_client *clients)
{
	struct ucred credentials;
	socklen_t credentials_length = sizeof(credentials);
	int slot;
	int fd = accept(listen_fd, NULL, NULL);

	if (fd < 0) {
		if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
			perror("micronux-deviced: accept");
		return;
	}
	for (slot = 0; slot < CLIENT_LIMIT; slot++) {
		if (clients[slot].state == CLIENT_FREE)
			break;
	}
	if (slot == CLIENT_LIMIT) {
		close(fd);
		return;
	}
	if (set_nonblocking(fd) < 0 ||
		getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials,
			&credentials_length) < 0) {
		close(fd);
		return;
	}
	clients[slot].fd = fd;
	clients[slot].state = CLIENT_REQUEST;
	clients[slot].uid = credentials.uid;
	clients[slot].capabilities = peer_capabilities(credentials.uid);
}

static int prepare_socket(void)
{
	struct sockaddr_un address;
	int fd;

	if (mkdir("/run", 0755) < 0 && errno != EEXIST)
		return -1;
	if (mkdir("/run/micronux", 0755) < 0 && errno != EEXIST)
		return -1;
	fd = socket(AF_UNIX, SOCK_SEQPACKET, 0);
	if (fd < 0)
		return -1;
	memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	strcpy(address.sun_path, MICRONUX_DEVICE_SOCKET_PATH);
	unlink(MICRONUX_DEVICE_SOCKET_PATH);
	if (bind(fd, (struct sockaddr *)&address, sizeof(address)) < 0 ||
		chmod(MICRONUX_DEVICE_SOCKET_PATH, 0666) < 0 ||
		listen(fd, CLIENT_LIMIT) < 0 || set_nonblocking(fd) < 0) {
		close(fd);
		unlink(MICRONUX_DEVICE_SOCKET_PATH);
		return -1;
	}
	return fd;
}

static int write_pid(void)
{
	FILE *file = fopen(SERVICE_PID_PATH, "w");
	int rc;

	if (file == NULL)
		return -1;
	rc = fprintf(file, "%lu\n", (unsigned long)getpid()) < 0 ? -1 : 0;
	if (fclose(file) != 0)
		rc = -1;
	return rc;
}

int main(void)
{
	struct service_client clients[CLIENT_LIMIT];
	struct sigaction action;
	struct pollfd poll_fds[CLIENT_LIMIT + 1];
	int listen_fd;
	int index;

	memset(clients, 0, sizeof(clients));
	for (index = 0; index < CLIENT_LIMIT; index++)
		clients[index].fd = -1;
	memset(&action, 0, sizeof(action));
	action.sa_handler = request_stop;
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGTERM, &action, NULL) < 0 ||
		sigaction(SIGINT, &action, NULL) < 0 ||
		signal(SIGPIPE, SIG_IGN) == SIG_ERR) {
		perror("micronux-deviced: signal");
		return 1;
	}
	setvbuf(stdout, NULL, _IOLBF, 0);
	listen_fd = prepare_socket();
	if (listen_fd < 0 || write_pid() < 0) {
		perror("micronux-deviced: initialize");
		return 1;
	}
	printf("MICRONUX:M8:SERVICE ready abi=%u.%u socket=%s\n",
		MICRONUX_DEVICE_ABI_MAJOR, MICRONUX_DEVICE_ABI_MINOR,
		MICRONUX_DEVICE_SOCKET_PATH);

	while (!stop_requested) {
		int rc;
		int64_t now_ms;
		uint32_t devices;

		poll_fds[0].fd = listen_fd;
		poll_fds[0].events = POLLIN;
		for (index = 0; index < CLIENT_LIMIT; index++) {
			poll_fds[index + 1].fd = clients[index].fd;
			poll_fds[index + 1].events = POLLIN;
		}
		do {
			rc = poll(poll_fds, CLIENT_LIMIT + 1, SERVICE_POLL_MS);
		} while (rc < 0 && errno == EINTR && !stop_requested);
		if (rc < 0) {
			if (errno == EINTR && stop_requested)
				break;
			perror("micronux-deviced: poll");
			break;
		}
		if (poll_fds[0].revents & POLLIN)
			accept_client(listen_fd, clients);
		for (index = 0; index < CLIENT_LIMIT; index++) {
			if (clients[index].state == CLIENT_FREE)
				continue;
			if (poll_fds[index + 1].revents &
				(POLLERR | POLLHUP | POLLNVAL)) {
				close_client(&clients[index]);
				continue;
			}
			if (clients[index].state == CLIENT_REQUEST &&
				(poll_fds[index + 1].revents & POLLIN))
				receive_request(&clients[index]);
		}

		now_ms = monotonic_ms();
		devices = current_devices();
		for (index = 0; index < CLIENT_LIMIT; index++) {
			if (clients[index].state != CLIENT_WAIT)
				continue;
			if ((devices & clients[index].wait_mask) ==
				clients[index].wait_mask)
				respond(&clients[index], MICRONUX_DEVICE_STATUS_OK,
					devices);
			else if (now_ms < 0 || now_ms >= clients[index].deadline_ms)
				respond(&clients[index], MICRONUX_DEVICE_STATUS_TIMEOUT,
					devices);
		}
	}

	for (index = 0; index < CLIENT_LIMIT; index++)
		close_client(&clients[index]);
	close(listen_fd);
	unlink(MICRONUX_DEVICE_SOCKET_PATH);
	unlink(SERVICE_PID_PATH);
	return 0;
}
