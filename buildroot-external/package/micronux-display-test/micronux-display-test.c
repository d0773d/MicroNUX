// SPDX-License-Identifier: MIT

#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <linux/kd.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define DISPLAY_WIDTH 800U
#define DISPLAY_HEIGHT 1280U
#define DISPLAY_STRIDE (DISPLAY_WIDTH * 2U)
#define TOUCH_NAME "MicroNUX GT9271 Touchscreen"
#define TOUCH_SYSFS "/sys/bus/platform/devices/500a0000.display/touch"
#define LOCK_PATH "/run/micronux-display.lock"
#define MAX_INPUT_DEVICES 8
#define TARGET_HALF 32U
#define TARGET_TOLERANCE 96U
#define DEFAULT_DRAW_SECONDS 5U
#define DEFAULT_TOUCH_TIMEOUT_MS 30000U
#define WRITE_CHUNK 64U
#define WRITE_GAP_NS 100000L
#define BITS_PER_LONG (sizeof(unsigned long) * 8U)
#define BITS_TO_LONGS(bits) (((bits) + BITS_PER_LONG - 1U) / BITS_PER_LONG)

static int console_fd = -1;
static int graphics_active;

struct touch_state {
	int x;
	int y;
	int down;
};

struct target {
	unsigned int x;
	unsigned int y;
	const char *name;
};

static const struct target targets[] = {
	{ 80U, 80U, "top-left" },
	{ 719U, 80U, "top-right" },
	{ 719U, 1199U, "bottom-right" },
	{ 80U, 1199U, "bottom-left" },
	{ 400U, 640U, "center" },
};

static int fail(const char *stage)
{
	const int saved_errno = errno;

	fprintf(stderr, "MICRONUX:M9:DISPLAY-TEST:FAIL stage=%s errno=%d\n",
		stage, saved_errno);
	return 1;
}

static int parse_uint(const char *text, unsigned int *value)
{
	char *end;
	unsigned long parsed;

	errno = 0;
	parsed = strtoul(text, &end, 10);
	if (errno != 0 || end == text || *end != '\0' || parsed > UINT32_MAX)
		return -1;
	*value = (unsigned int)parsed;
	return 0;
}

static int bit_is_set(const unsigned long *bits, unsigned int bit)
{
	return !!(bits[bit / BITS_PER_LONG] &
		  (1UL << (bit % BITS_PER_LONG)));
}

static int set_framebuffer_blank(int blank)
{
	int fd;
	int first_error = 0;

	fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
	if (fd < 0)
		return -1;
	if (ioctl(fd, FBIOBLANK, blank) < 0)
		first_error = errno;
	if (close(fd) < 0 && !first_error)
		first_error = errno;
	if (first_error) {
		errno = first_error;
		return -1;
	}
	return 0;
}

static int restore_console_state(void)
{
	int first_error = 0;

	if (graphics_active) {
		if (set_framebuffer_blank(FB_BLANK_POWERDOWN) < 0)
			first_error = errno;
		if (ioctl(console_fd, KDSETMODE, KD_TEXT) < 0 && !first_error)
			first_error = errno;
		if (set_framebuffer_blank(FB_BLANK_UNBLANK) < 0 && !first_error)
			first_error = errno;
	}
	graphics_active = 0;
	if (console_fd >= 0) {
		if (close(console_fd) < 0 && !first_error)
			first_error = errno;
		console_fd = -1;
	}
	if (first_error) {
		errno = first_error;
		return -1;
	}
	return 0;
}

static void restore_console(void)
{
	(void)restore_console_state();
}

static void handle_signal(int signal_number)
{
	if (restore_console_state() != 0)
		_exit(125);
	_exit(128 + signal_number);
}

static int acquire_graphics(void)
{
	console_fd = open("/dev/tty1", O_RDWR | O_CLOEXEC);
	if (console_fd < 0)
		return fail("tty1-open");
	if (ioctl(console_fd, KDSETMODE, KD_GRAPHICS) < 0)
		return fail("kd-graphics");
	graphics_active = 1;
	if (set_framebuffer_blank(FB_BLANK_UNBLANK) < 0)
		return fail("framebuffer-unblank");
	return 0;
}

static int acquire_lock(void)
{
	int fd = open(LOCK_PATH, O_RDWR | O_CREAT | O_CLOEXEC, 0600);

	if (fd < 0)
		return -1;
	if (flock(fd, LOCK_EX | LOCK_NB) < 0) {
		(void)close(fd);
		return -1;
	}
	return fd;
}

static int open_framebuffer(struct fb_fix_screeninfo *fix,
			    struct fb_var_screeninfo *var)
{
	int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);

	if (fd < 0)
		return -1;
	if (ioctl(fd, FBIOGET_FSCREENINFO, fix) < 0 ||
	    ioctl(fd, FBIOGET_VSCREENINFO, var) < 0 ||
	    var->xres != DISPLAY_WIDTH || var->yres != DISPLAY_HEIGHT ||
	    var->bits_per_pixel != 16U || fix->line_length != DISPLAY_STRIDE ||
	    fix->smem_len != DISPLAY_STRIDE * DISPLAY_HEIGHT) {
		(void)close(fd);
		errno = EPROTO;
		return -1;
	}
	return fd;
}

static int touch_capabilities_ok(int fd)
{
	unsigned long ev_bits[BITS_TO_LONGS(EV_MAX + 1U)] = { 0 };
	unsigned long key_bits[BITS_TO_LONGS(KEY_MAX + 1U)] = { 0 };
	unsigned long abs_bits[BITS_TO_LONGS(ABS_MAX + 1U)] = { 0 };
	struct input_absinfo x;
	struct input_absinfo y;

	if (ioctl(fd, EVIOCGBIT(0, sizeof(ev_bits)), ev_bits) < 0 ||
	    ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) < 0 ||
	    ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(abs_bits)), abs_bits) < 0 ||
	    ioctl(fd, EVIOCGABS(ABS_X), &x) < 0 ||
	    ioctl(fd, EVIOCGABS(ABS_Y), &y) < 0)
		return 0;
	return bit_is_set(ev_bits, EV_KEY) && bit_is_set(ev_bits, EV_ABS) &&
		bit_is_set(key_bits, BTN_TOUCH) &&
		bit_is_set(abs_bits, ABS_X) && bit_is_set(abs_bits, ABS_Y) &&
		x.minimum == 0 && x.maximum == (int)DISPLAY_WIDTH - 1 &&
		y.minimum == 0 && y.maximum == (int)DISPLAY_HEIGHT - 1;
}

static int open_touch(char *path, size_t path_size)
{
	char name[128];
	int fd;
	int index;

	for (index = 0; index < MAX_INPUT_DEVICES; ++index) {
		if (snprintf(path, path_size, "/dev/input/event%d", index) >=
		    (int)path_size)
			continue;
		fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
		if (fd < 0)
			continue;
		memset(name, 0, sizeof(name));
		if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) >= 0 &&
		    strcmp(name, TOUCH_NAME) == 0) {
			if (touch_capabilities_ok(fd))
				return fd;
			(void)close(fd);
			errno = EPROTO;
			return -1;
		}
		(void)close(fd);
	}
	errno = ENODEV;
	return -1;
}

static int read_touch_status(char *status, size_t status_size)
{
	ssize_t length;
	int first_error = 0;
	int fd = open(TOUCH_SYSFS, O_RDONLY | O_CLOEXEC);

	if (fd < 0)
		return -1;
	length = read(fd, status, status_size - 1U);
	if (length < 0)
		first_error = errno;
	else if (length == 0)
		first_error = EIO;
	if (close(fd) < 0 && !first_error)
		first_error = errno;
	if (first_error) {
		errno = first_error;
		return -1;
	}
	status[length] = '\0';
	return 0;
}

static int touch_status_ready(const char *status)
{
	char canonical[256];
	unsigned int address;
	unsigned int interval_ms;
	unsigned int reads;
	unsigned int errors;
	unsigned int down;
	int canonical_length;
	int consumed = -1;

	if (sscanf(status,
		   "ready product=9271 address=0x%x mode=poll interval_ms=%u reads=%u errors=%u down=%u%n",
		   &address, &interval_ms, &reads, &errors, &down,
		   &consumed) != 5 || consumed < 0 ||
	    strcmp(status + consumed, "\n") != 0)
		return 0;
	if ((address != 0x5dU && address != 0x14U) || interval_ms < 5U ||
	    interval_ms > 100U || down > 1U)
		return 0;
	canonical_length = snprintf(canonical, sizeof(canonical),
		"ready product=9271 address=0x%02x mode=poll interval_ms=%u reads=%u errors=%u down=%u\n",
		address, interval_ms, reads, errors, down);
	if (canonical_length < 0 || canonical_length >= (int)sizeof(canonical))
		return 0;
	return strcmp(status, canonical) == 0;
}

static int write_paced(int fd, const void *buffer, size_t length, off_t offset)
{
	const uint8_t *bytes = buffer;
	size_t written = 0;

	while (written < length) {
		size_t chunk = length - written;
		ssize_t result;

		if (chunk > WRITE_CHUNK)
			chunk = WRITE_CHUNK;
		result = pwrite(fd, bytes + written, chunk, offset + written);
		if (result <= 0)
			return -1;
		written += (size_t)result;
		if (written < length) {
			struct timespec start;
			struct timespec now;
			int64_t elapsed;

			if (clock_gettime(CLOCK_MONOTONIC, &start) != 0)
				return -1;
			do {
				if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
					return -1;
				elapsed = (int64_t)(now.tv_sec - start.tv_sec) *
					INT64_C(1000000000) + now.tv_nsec - start.tv_nsec;
			} while (elapsed < WRITE_GAP_NS);
		}
	}
	return 0;
}

static uint16_t color_for(unsigned int x, unsigned int y)
{
	if (x < 8U || x >= DISPLAY_WIDTH - 8U ||
	    y < 8U || y >= DISPLAY_HEIGHT - 8U)
		return UINT16_C(0xffff);
	if (y < DISPLAY_HEIGHT / 4U)
		return UINT16_C(0x001f);
	if (y < DISPLAY_HEIGHT / 2U)
		return UINT16_C(0x07e0);
	if (y < (DISPLAY_HEIGHT * 3U) / 4U)
		return UINT16_C(0xf800);
	return UINT16_C(0x1082);
}

static int draw_test_pattern(int fd)
{
	uint16_t row[DISPLAY_WIDTH];
	unsigned int x;
	unsigned int y;

	for (y = 0; y < DISPLAY_HEIGHT; ++y) {
		for (x = 0; x < DISPLAY_WIDTH; ++x)
			row[x] = color_for(x, y);
		if (write_paced(fd, row, sizeof(row),
				 (off_t)y * DISPLAY_STRIDE) != 0)
			return -1;
	}
	return 0;
}

static int draw_target(int fd, unsigned int center_x,
		       unsigned int center_y, uint16_t color)
{
	uint16_t row[TARGET_HALF * 2U + 1U];
	unsigned int x0 = center_x - TARGET_HALF;
	unsigned int y0 = center_y - TARGET_HALF;
	unsigned int y;

	for (y = 0; y < sizeof(row) / sizeof(row[0]); ++y)
		row[y] = color;
	for (y = 0; y < sizeof(row) / sizeof(row[0]); ++y) {
		if (write_paced(fd, row, sizeof(row),
				 (off_t)(y0 + y) * DISPLAY_STRIDE + x0 * 2U) != 0)
			return -1;
	}
	return 0;
}

static int wait_touch_state(int fd, struct touch_state *state, int want_down,
			    unsigned int timeout_ms)
{
	struct input_event events[16];
	struct timespec start;

	if (clock_gettime(CLOCK_MONOTONIC, &start) != 0)
		return -1;
	for (;;) {
		struct pollfd pollfd = { .fd = fd, .events = POLLIN };
		struct timespec now;
		unsigned int elapsed;
		ssize_t bytes;
		size_t index;

		if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
			return -1;
		elapsed = (unsigned int)((now.tv_sec - start.tv_sec) * 1000L +
			(now.tv_nsec - start.tv_nsec) / 1000000L);
		if (elapsed >= timeout_ms) {
			errno = ETIMEDOUT;
			return -1;
		}
		if (poll(&pollfd, 1, (int)(timeout_ms - elapsed)) <= 0) {
			errno = ETIMEDOUT;
			return -1;
		}
		bytes = read(fd, events, sizeof(events));
		if (bytes < 0 && errno == EAGAIN)
			continue;
		if (bytes <= 0 || bytes % (ssize_t)sizeof(events[0]) != 0)
			return -1;
		for (index = 0; index < (size_t)bytes / sizeof(events[0]); ++index) {
			const struct input_event *event = &events[index];

			if (event->type == EV_ABS && event->code == ABS_X)
				state->x = event->value;
			else if (event->type == EV_ABS && event->code == ABS_Y)
				state->y = event->value;
			else if (event->type == EV_KEY &&
				 event->code == BTN_TOUCH)
				state->down = !!event->value;
			else if (event->type == EV_SYN &&
				 event->code == SYN_REPORT &&
				 state->down == want_down)
				return 0;
		}
	}
}

static unsigned int distance(unsigned int a, unsigned int b)
{
	return a > b ? a - b : b - a;
}

static int run_check(void)
{
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	char status[256];
	char touch_path[64];
	int fb = open_framebuffer(&fix, &var);
	int touch;

	if (fb < 0)
		return fail("framebuffer-check");
	touch = open_touch(touch_path, sizeof(touch_path));
	if (touch < 0 && errno != ENODEV)
		goto touch_check_failed;
	if (read_touch_status(status, sizeof(status)) != 0) {
		if (touch >= 0)
			(void)close(touch);
		(void)close(fb);
		return fail("touch-status-read");
	}
	if (touch >= 0 && !touch_status_ready(status)) {
		errno = EPROTO;
		goto touch_status_failed;
	}
	if (touch < 0 && strcmp(status, "unavailable\n") != 0) {
		errno = EPROTO;
		goto touch_status_failed;
	}
	if (touch >= 0) {
		printf("MICRONUX:M9:DISPLAY-TEST:PASS mode=check fb=800x1280-rgb565 stride=1600 input=%s %s",
		       touch_path, status);
		(void)close(touch);
	} else {
		printf("MICRONUX:M9:DISPLAY-TEST:PASS mode=check fb=800x1280-rgb565 stride=1600 input=unavailable %s",
		       status);
	}
	(void)close(fb);
	return 0;

touch_status_failed:
	if (touch >= 0)
		(void)close(touch);
touch_check_failed:
	(void)close(fb);
	return fail("touch-check");
}

static int run_draw(unsigned int seconds)
{
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	int fb;

	if (acquire_graphics() != 0)
		return 1;
	fb = open_framebuffer(&fix, &var);
	if (fb < 0)
		return fail("framebuffer-draw-open");
	if (draw_test_pattern(fb) != 0) {
		(void)close(fb);
		return fail("framebuffer-draw-write");
	}
	printf("MICRONUX:M9:DISPLAY-TEST state=visible mode=draw seconds=%u pace_bytes=%u gap_ns=%ld\n",
	       seconds, WRITE_CHUNK, WRITE_GAP_NS);
	(void)sleep(seconds);
	(void)close(fb);
	if (restore_console_state() != 0)
		return fail("console-restore");
	printf("MICRONUX:M9:DISPLAY-TEST:PASS mode=draw console=restored\n");
	return 0;
}

static int run_touch(unsigned int timeout_ms)
{
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	struct touch_state state = { 0 };
	char touch_path[64];
	unsigned int index;
	int result = 1;
	int touch;
	int fb;

	if (acquire_graphics() != 0)
		return 1;
	fb = open_framebuffer(&fix, &var);
	if (fb < 0)
		return fail("framebuffer-touch-open");
	touch = open_touch(touch_path, sizeof(touch_path));
	if (touch < 0) {
		result = fail("touch-open");
		goto close_framebuffer;
	}
	if (draw_test_pattern(fb) != 0) {
		result = fail("touch-background");
		goto close_touch;
	}

	for (index = 0; index < sizeof(targets) / sizeof(targets[0]); ++index) {
		const struct target *target = &targets[index];

		if (draw_target(fb, target->x, target->y, UINT16_C(0xffe0)) != 0) {
			result = fail("touch-target-draw");
			goto close_touch;
		}
		printf("MICRONUX:M9:TOUCH-TARGET state=waiting index=%u name=%s x=%u y=%u\n",
		       index + 1U, target->name, target->x, target->y);
		if (wait_touch_state(touch, &state, 1, timeout_ms) != 0) {
			result = fail("touch-press-timeout");
			goto close_touch;
		}
		if (distance((unsigned int)state.x, target->x) >
		    TARGET_TOLERANCE ||
		    distance((unsigned int)state.y, target->y) >
		    TARGET_TOLERANCE) {
			errno = ERANGE;
			fprintf(stderr,
				"MICRONUX:M9:TOUCH-TARGET:FAIL index=%u expected=%u,%u actual=%d,%d tolerance=%u\n",
				index + 1U, target->x, target->y, state.x, state.y,
				TARGET_TOLERANCE);
			goto close_touch;
		}
		if (draw_target(fb, target->x, target->y, UINT16_C(0x07e0)) != 0) {
			result = fail("touch-target-pass-draw");
			goto close_touch;
		}
		printf("MICRONUX:M9:TOUCH-TARGET:PASS index=%u name=%s actual=%d,%d\n",
		       index + 1U, target->name, state.x, state.y);
		if (wait_touch_state(touch, &state, 0, timeout_ms) != 0) {
			result = fail("touch-release-timeout");
			goto close_touch;
		}
	}

	result = 0;
close_touch:
	(void)close(touch);
close_framebuffer:
	(void)close(fb);
	if (result != 0)
		return result;
	if (restore_console_state() != 0)
		return fail("console-restore");
	printf("MICRONUX:M9:DISPLAY-TEST:PASS mode=touch points=5 input=%s console=restored\n",
	       touch_path);
	return 0;
}

static void usage(const char *program)
{
	fprintf(stderr,
		"usage: %s check | draw [seconds] | touch [timeout-ms]\n",
		program);
}

int main(int argc, char **argv)
{
	unsigned int value;
	int lock_fd;
	int result;

	setvbuf(stdout, NULL, _IONBF, 0);
	if (argc < 2 || argc > 3) {
		usage(argv[0]);
		return 2;
	}
	lock_fd = acquire_lock();
	if (lock_fd < 0)
		return fail("display-lock");
	(void)atexit(restore_console);
	(void)signal(SIGINT, handle_signal);
	(void)signal(SIGTERM, handle_signal);

	if (strcmp(argv[1], "check") == 0 && argc == 2) {
		result = run_check();
	} else if (strcmp(argv[1], "draw") == 0) {
		value = DEFAULT_DRAW_SECONDS;
		if ((argc == 3 && parse_uint(argv[2], &value) != 0) ||
		    value > 300U) {
			usage(argv[0]);
			result = 2;
		} else {
			result = run_draw(value);
		}
	} else if (strcmp(argv[1], "touch") == 0) {
		value = DEFAULT_TOUCH_TIMEOUT_MS;
		if ((argc == 3 && parse_uint(argv[2], &value) != 0) ||
		    value < 1000U || value > 300000U) {
			usage(argv[0]);
			result = 2;
		} else {
			result = run_touch(value);
		}
	} else {
		usage(argv[0]);
		result = 2;
	}

	if (restore_console_state() != 0 && result == 0)
		result = fail("console-restore");
	(void)close(lock_fd);
	return result;
}
