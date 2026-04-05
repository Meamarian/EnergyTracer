/* SPDX-License-Identifier: BSD-3-Clause
 * Copyright(c) 2010-2018 Intel Corporation
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <inttypes.h>
#include <sys/types.h>
#include <string.h>
#include <sys/queue.h>
#include <stdarg.h>
#include <errno.h>
#include <getopt.h>
#include <unistd.h>
#include <signal.h>
#include <limits.h>
#include <sys/stat.h>
#include <time.h>
#include <arpa/inet.h>
#include <math.h>
#include <jansson.h>
#include <rte_common.h>
#include <rte_byteorder.h>
#include <rte_log.h>
#include <rte_malloc.h>
#include <rte_memory.h>
#include <rte_memcpy.h>
#include <rte_eal.h>
#include <rte_launch.h>
#include <rte_atomic.h>
#include <rte_cycles.h>
#include <rte_prefetch.h>
#include <rte_lcore.h>
#include <rte_per_lcore.h>
#include <rte_branch_prediction.h>
#include <rte_interrupts.h>
#include <rte_random.h>
#include <rte_debug.h>
#include <rte_ether.h>
#include <rte_ethdev.h>
#include <rte_mempool.h>
#include <rte_mbuf.h>
#include <rte_ip.h>
#include <rte_tcp.h>
#include <rte_udp.h>
#include <rte_string_fns.h>
#include <rte_timer.h>
#include <rte_power.h>
#include <rte_spinlock.h>
#include <rte_power_empty_poll.h>
#include <rte_metrics.h>
#include <rte_telemetry.h>
#include <rte_hash.h>
#include <rte_jhash.h>
#include <rte_ring.h>
/* Add Your Tracing Instrumentation Here: include DPDK native tracing support so runtime trace gating and saving can be controlled in this file. */
#include <rte_trace.h>

#include "perf_core.h"
#include "main.h"

//-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
#define UDP_PORT_GTPU 2152
#define UDP_PORT_HOST_GTP 2153
#define UDP_PORT_HOST_RLC 8044
#define UDP_SPORT_RLC 8042
#define UDP_SPORT_RLCS 8052
#define UDP_SPORT_HRLC 8043
#define UDP_SPORT_HRLCS 8053
#define UDP_PORT_BUFFER 12345
#define ETHERTYPE_IPV4 0x0800
#define HASH_TABLE_SIZE 64000
#define HASH_TABLE_NAME "teid_qfi_table"
#define UL_HASH_TABLE_SIZE 64000
#define UL_HASH_TABLE_NAME "ul_teid_table"
#define RTE_PKTMBUF_HEADROOM 128
#define HDR_MBUF_DATA_SIZE (2 * RTE_PKTMBUF_HEADROOM)
#define CLONE_POOL_SIZE 100000U
#define MAX_PORTS 16
unsigned int max_used_lcore = 0;
struct rte_mbuf *clone_mbufs[MAX_PORTS][CLONE_POOL_SIZE];
struct rte_mempool *my_clone_pool[MAX_PORTS];

#define HASH_TABLE_SIZE 64000
#define HASH_TABLE_NAME "teid_qfi_table"
#define UL_HASH_TABLE_SIZE 64000
#define UL_HASH_TABLE_NAME "ul_teid_table"

struct rte_hash *teid_qfi_table;
struct rte_hash *ul_teid_table;

struct udp_hdr {
	uint16_t src_port;    /**< UDP source port. */
	uint16_t dst_port;    /**< UDP destination port. */
	uint16_t dgram_len;   /**< UDP datagram length */
	uint16_t dgram_cksum; /**< UDP datagram checksum */
} __attribute__((__packed__));

struct ipv4_hdr {
	uint8_t  version_ihl;		/**< version and header length */
	uint8_t  type_of_service;	/**< type of service */
	uint16_t total_length;		/**< length of packet */
	uint16_t packet_id;		/**< packet ID */
	uint16_t fragment_offset;	/**< fragmentation offset */
	uint8_t  time_to_live;		/**< time to live */
	uint8_t  next_proto_id;		/**< protocol ID */
	uint32_t hdr_checksum;		/**< header checksum */
	uint32_t src_addr;		/**< source address */
	uint32_t dst_addr;		/**< destination address */
} __attribute__((__packed__));

/* Define the hash table structures */
struct gtp_header {
	uint8_t flags;
	uint8_t type;
	uint16_t length;
	uint32_t teid;
	uint16_t seq_num;
	uint8_t qfi;
} __attribute__((__packed__));

struct rlc_ack_mode_header {
	uint8_t dc_p_si_r; // dc:1, p:1, si:2, r:2
	uint16_t sn;
	uint32_t teid;
	//uint8_t rbnum;
} __attribute__((__packed__));

struct pdcp_header {
	uint8_t dc_r;
	//uint8_t r;
	uint16_t sn;
} __attribute__((__packed__));

struct sdap_header {
	uint8_t rdi_rqi_qfi;
} __attribute__((__packed__));

// struct payload_header {
//     uint64_t rdi_rqi_qfi;
// 	uint64_t rdi_rqi_qfi;
// 	uint64_t rdi_rqi_qfi;
// } __attribute__((__packed__));

struct metadata {
	uint32_t teid;
	uint8_t *rbnumber;
	uint8_t *rqibool;
};

struct teid_qfi_entry {
	uint32_t teid;
	uint8_t rbnumber;
	uint8_t rqibool;
};

struct ul_teid_entry {
	uint32_t teid;
};

//***************************************************************
static struct rte_hash_parameters ut_params = {
	.name = "BufferTable",
	.entries = 1024 * 256 * 1024,
	.key_len = sizeof(uint64_t),
	.hash_func = rte_jhash,
	.extra_flag = RTE_HASH_EXTRA_FLAGS_EXT_TABLE,
	//.extra_flag=RTE_HASH_EXTRA_FLAGS_RW_CONCURRENCY,
	.socket_id = 0,
};

typedef struct rte_hash lookup_struct_t1;
static lookup_struct_t1 *buffer_table;
static lookup_struct_t1 *buffer_table_teid;
//***************************************************************
//-*-*-*-*-*-*-*-*-*-*

#define RTE_LOGTYPE_L3FWD_POWER RTE_LOGTYPE_USER1

#define MAX_PKT_BURST 32

/* Add Your Tracing Instrumentation Here: define runtime trace gating state, trigger conditions, anchor bookkeeping, and bounded trace-capture policy for RX polling. */
static volatile int first_rx_anchor_printed = 0;
static char g_trace_dir[PATH_MAX] = {0};

#define TRACE_RX_EVENT_PATTERN   "lib.ethdev.rx.burst"
#define TRACE_START_NB_RX        MAX_PKT_BURST
#define TRACE_START_NB_RX        0
#define TRACE_STOP_PKT_LIMIT     12000ULL
#define TRACE_CAPTURE_WAITING    0
#define TRACE_CAPTURE_STARTING   1
#define TRACE_CAPTURE_STARTED    2
#define TRACE_CAPTURE_STOPPED    3

static volatile int trace_capture_state = TRACE_CAPTURE_WAITING;
static volatile uint64_t trace_captured_pkts = 0;

/* Add Your Tracing Instrumentation Here: capture the user-provided trace directory from argv so the app can store anchor.txt and trace output in a chosen run folder. */
static void
capture_trace_dir_from_argv(int argc, char **argv)
{
	int i;
	const char *prefix = "--trace-dir=";
	size_t prefix_len = strlen(prefix);

	for (i = 1; i < argc; i++) {
		if (strncmp(argv[i], prefix, prefix_len) == 0) {
			snprintf(g_trace_dir, sizeof(g_trace_dir), "%s",
				 argv[i] + prefix_len);
			return;
		}

		if (strcmp(argv[i], "--trace-dir") == 0 && (i + 1) < argc) {
			snprintf(g_trace_dir, sizeof(g_trace_dir), "%s", argv[i + 1]);
			return;
		}
	}
}

/* Add Your Tracing Instrumentation Here: write and print a one-time timing anchor so trace timestamps can later be aligned with MONOTONIC_RAW time and TSC. */
static void
print_first_rx_anchor(unsigned lcore_id, uint16_t portid, uint16_t nb_rx)
{
	struct timespec ts;
	uint64_t mono_raw_ns;
	uint64_t tsc;
	uint64_t tsc_hz;

	clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
	mono_raw_ns = (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;

	// tsc = rte_get_tsc_cycles();
	tsc = rte_get_tsc_cycles() & ((1ULL << 48) - 1);
	tsc_hz = rte_get_tsc_hz();

	if (g_trace_dir[0] != '\0') {
		char anchor_path[PATH_MAX];
		FILE *f;

		mkdir(g_trace_dir, 0755);

		snprintf(anchor_path, sizeof(anchor_path), "%s/anchor.txt", g_trace_dir);
		f = fopen(anchor_path, "w");
		if (f != NULL) {
			fprintf(f,
				"mono_raw_ns=%" PRIu64 "\n"
				"tsc=%" PRIu64 "\n"
				"tsc_hz=%" PRIu64 "\n"
				"lcore=%u\n"
				"port=%u\n"
				"nb_rx=%u\n",
				mono_raw_ns, tsc, tsc_hz, lcore_id, portid, nb_rx);
			fclose(f);
		}
	}

	printf("TIME_ANCHOR first_rx mono_raw_ns=%" PRIu64
	       " tsc=%" PRIu64
	       " tsc_hz=%" PRIu64
	       " lcore=%u port=%u nb_rx=%u\n",
	       mono_raw_ns, tsc, tsc_hz, lcore_id, portid, nb_rx);

	fflush(stdout);
}

/* Add Your Tracing Instrumentation Here: initialize runtime trace gating by disabling all tracepoints first, then arming only the selected RX event for later activation. */
static void
trace_runtime_init(void)
{
	int rc;

	if (!rte_trace_is_enabled()) {
		RTE_LOG(INFO, L3FWD_POWER,
			"trace subsystem is not enabled; runtime trace gating is inactive\n");
		return;
	}

	/* Disable everything first. We only enable rx-burst later on trigger. */
	rc = rte_trace_pattern("*", false);
	if (rc < 0) {
		RTE_LOG(ERR, L3FWD_POWER,
			"failed to disable all tracepoints at init: rc=%d\n", rc);
	}

	rc = rte_trace_pattern(TRACE_RX_EVENT_PATTERN, false);
	if (rc < 0) {
		RTE_LOG(ERR, L3FWD_POWER,
			"failed to explicitly disable %s at init: rc=%d\n",
			TRACE_RX_EVENT_PATTERN, rc);
	}

	trace_capture_state = TRACE_CAPTURE_WAITING;
	trace_captured_pkts = 0;

	RTE_LOG(INFO, L3FWD_POWER,
		"trace gating armed: start after first nb_rx == %u, "
		"capture only %s, stop after %" PRIu64 " packets\n",
		TRACE_START_NB_RX, TRACE_RX_EVENT_PATTERN, TRACE_STOP_PKT_LIMIT);
}

/* Add Your Tracing Instrumentation Here: turn on the selected tracepoint at the chosen RX trigger, emit the timing anchor, and begin bounded packet capture. */
static void
trace_start_capture(unsigned lcore_id, uint16_t portid, uint16_t nb_rx)
{
	int rc;

	rc = rte_trace_pattern(TRACE_RX_EVENT_PATTERN, true);
	if (rc < 0) {
		RTE_LOG(ERR, L3FWD_POWER,
			"failed to enable %s: rc=%d\n",
			TRACE_RX_EVENT_PATTERN, rc);
		trace_capture_state = TRACE_CAPTURE_STOPPED;
		return;
	}

	if (__sync_bool_compare_and_swap(&first_rx_anchor_printed, 0, 1))
		print_first_rx_anchor(lcore_id, portid, nb_rx);

	trace_captured_pkts = 0;
	trace_capture_state = TRACE_CAPTURE_STARTED;

	RTE_LOG(INFO, L3FWD_POWER,
		"TRACE STARTED on lcore=%u port=%u trigger_nb_rx=%u; "
		"trigger burst itself is not recorded; capturing next RX events "
		"until %" PRIu64 " packets\n",
		lcore_id, portid, nb_rx, TRACE_STOP_PKT_LIMIT);
}

/* Add Your Tracing Instrumentation Here: stop the selected tracepoint when the capture budget is reached and save the trace buffer to disk. */
static void
trace_stop_capture(uint64_t total_pkts)
{
	int rc;

	rc = rte_trace_pattern(TRACE_RX_EVENT_PATTERN, false);
	if (rc < 0) {
		RTE_LOG(ERR, L3FWD_POWER,
			"failed to disable %s at stop: rc=%d\n",
			TRACE_RX_EVENT_PATTERN, rc);
	} else {
		RTE_LOG(INFO, L3FWD_POWER,
			"TRACE STOPPED after %" PRIu64 " packets\n", total_pkts);
	}

	rc = rte_trace_save();
	if (rc < 0) {
		RTE_LOG(ERR, L3FWD_POWER, "rte_trace_save() failed: rc=%d\n", rc);
	} else {
		RTE_LOG(INFO, L3FWD_POWER, "trace saved successfully\n");
	}
}

/* Add Your Tracing Instrumentation Here: hook every RX poll result into the trace controller so the app can anchor once, arm on trigger, count captured packets, and stop automatically. */
static inline void
trace_capture_handle_rx(unsigned lcore_id, uint16_t portid, uint16_t nb_rx)
{
	bool trace_started_now = false;

	/*
	 * Write anchor once on the first observed poll, even if nb_rx == 0.
	 * This gives us anchor.txt for idle/no-RX experiments too.
	 * It does NOT start tracing yet.
	 */
	if (rte_trace_is_enabled() &&
	    __sync_bool_compare_and_swap(&first_rx_anchor_printed, 0, 1)) {
		print_first_rx_anchor(lcore_id, portid, nb_rx);
	}

	/*
	 * Arm tracing only after the configured trigger burst.
	 * NOTE: this trigger burst itself is not captured, because
	 * we only know nb_rx after rte_eth_rx_burst() returns.
	 */
	if (rte_trace_is_enabled() &&
	    trace_capture_state == TRACE_CAPTURE_WAITING &&
	    nb_rx == TRACE_START_NB_RX &&
	    __sync_bool_compare_and_swap(&trace_capture_state,
					 TRACE_CAPTURE_WAITING,
					 TRACE_CAPTURE_STARTING)) {
		trace_start_capture(lcore_id, portid, nb_rx);
		trace_started_now = true;
	}

	/*
	 * Count captured packets only after tracing is already on.
	 * We intentionally do not count the trigger burst itself.
	 */
	if (!trace_started_now &&
	    trace_capture_state == TRACE_CAPTURE_STARTED &&
	    nb_rx > 0) {
		uint64_t total_pkts;

		total_pkts = __sync_add_and_fetch(&trace_captured_pkts,
						  (uint64_t)nb_rx);

		if (total_pkts >= TRACE_STOP_PKT_LIMIT &&
		    __sync_bool_compare_and_swap(&trace_capture_state,
						 TRACE_CAPTURE_STARTED,
						 TRACE_CAPTURE_STOPPED)) {
			trace_stop_capture(total_pkts);
		}
	}
}

//uncomment for old anchor file method

// static inline void
// trace_capture_handle_rx(unsigned lcore_id, uint16_t portid, uint16_t nb_rx)
// {
// 	bool trace_started_now = false;

// 	/*
// 	 * Arm tracing only after the first full burst.
// 	 * NOTE: this trigger burst itself is not captured, because
// 	 * we only know nb_rx after rte_eth_rx_burst() returns.
// 	 */
// 	if (rte_trace_is_enabled() &&
// 	    trace_capture_state == TRACE_CAPTURE_WAITING &&
// 	    nb_rx == TRACE_START_NB_RX &&
// 	    __sync_bool_compare_and_swap(&trace_capture_state,
// 					 TRACE_CAPTURE_WAITING,
// 					 TRACE_CAPTURE_STARTING)) {
// 		trace_start_capture(lcore_id, portid, nb_rx);
// 		trace_started_now = true;
// 	}

// 	/*
// 	 * Count captured packets only after tracing is already on.
// 	 * We intentionally do not count the trigger burst itself.
// 	 */
// 	if (!trace_started_now &&
// 	    trace_capture_state == TRACE_CAPTURE_STARTED &&
// 	    nb_rx > 0) {
// 		uint64_t total_pkts;

// 		total_pkts = __sync_add_and_fetch(&trace_captured_pkts,
// 						  (uint64_t)nb_rx);

// 		if (total_pkts >= TRACE_STOP_PKT_LIMIT &&
// 		    __sync_bool_compare_and_swap(&trace_capture_state,
// 						 TRACE_CAPTURE_STARTED,
// 						 TRACE_CAPTURE_STOPPED)) {
// 			trace_stop_capture(total_pkts);
// 		}
// 	}
// }

/* 100 ms interval */
#define TIMER_NUMBER_PER_SECOND           10
/* (10ms) */
#define INTERVALS_PER_SECOND             100
/* 100000 us */
#define SCALING_PERIOD                    (1000000 / TIMER_NUMBER_PER_SECOND)
#define SCALING_DOWN_TIME_RATIO_THRESHOLD 0.25

#define APP_LOOKUP_EXACT_MATCH          0
#define APP_LOOKUP_LPM                  1
#define DO_RFC_1812_CHECKS

#ifndef APP_LOOKUP_METHOD
#define APP_LOOKUP_METHOD             APP_LOOKUP_LPM
#endif

#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
#include <rte_hash.h>
#elif (APP_LOOKUP_METHOD == APP_LOOKUP_LPM)
#include <rte_lpm.h>
#else
#error "APP_LOOKUP_METHOD set to incorrect value"
#endif

#ifndef IPv6_BYTES
#define IPv6_BYTES_FMT "%02x%02x:%02x%02x:%02x%02x:%02x%02x:"\
		       "%02x%02x:%02x%02x:%02x%02x:%02x%02x"
#define IPv6_BYTES(addr) \
	addr[0],  addr[1], addr[2],  addr[3], \
	addr[4],  addr[5], addr[6],  addr[7], \
	addr[8],  addr[9], addr[10], addr[11],\
	addr[12], addr[13],addr[14], addr[15]
#endif

#define MAX_JUMBO_PKT_LEN  9600

#define IPV6_ADDR_LEN 16

#define MEMPOOL_CACHE_SIZE 256

/*
 * This expression is used to calculate the number of mbufs needed depending on
 * user input, taking into account memory for rx and tx hardware rings, cache
 * per lcore and mtable per port per lcore. RTE_MAX is used to ensure that
 * NB_MBUF never goes below a minimum value of 8192.
 */

#define NB_MBUF RTE_MAX ( \
	(nb_ports * nb_rx_queue * nb_rxd + \
	nb_ports * nb_lcores * MAX_PKT_BURST + \
	nb_ports * n_tx_queue * nb_txd + \
	nb_lcores * MEMPOOL_CACHE_SIZE), \
	(unsigned)8192)

#define BURST_TX_DRAIN_US 10 /* TX drain every ~100us */

#define NB_SOCKETS 8

/* Configure how many packets ahead to prefetch, when reading packets */
#define PREFETCH_OFFSET 3

/*
 * Configurable number of RX/TX ring descriptors
 */
#define RTE_TEST_RX_DESC_DEFAULT 1024
#define RTE_TEST_TX_DESC_DEFAULT 1024

/*
 * These two thresholds were decided on by running the training algorithm on
 * a 2.5GHz Xeon. These defaults can be overridden by supplying non-zero values
 * for the med_threshold and high_threshold parameters on the command line.
 */
#define EMPTY_POLL_MED_THRESHOLD 350000UL
#define EMPTY_POLL_HGH_THRESHOLD 580000UL

#define NUM_TELSTATS RTE_DIM(telstats_strings)

static uint16_t nb_rxd = RTE_TEST_RX_DESC_DEFAULT;
static uint16_t nb_txd = RTE_TEST_TX_DESC_DEFAULT;

/* mask of enabled ports */
static uint32_t L3FWD_POWER_enabled_port_mask = 0;

/* list of enabled ports */
static uint32_t L3FWD_POWER_dst_ports[RTE_MAX_ETHPORTS];

/* ethernet addresses of ports */
static struct rte_ether_addr ports_eth_addr[RTE_MAX_ETHPORTS];

/* ethernet addresses of ports */
static rte_spinlock_t locks[RTE_MAX_ETHPORTS];

/* mask of enabled ports */
static uint32_t enabled_port_mask = 0;
/* Ports set in promiscuous mode off by default. */
static int promiscuous_on = 0;
/* NUMA is enabled by default. */
static int numa_on = 1;
static bool empty_poll_stop;
static bool empty_poll_train;
volatile bool quit_signal;
static struct ep_params *ep_params;
static struct ep_policy policy;
static long ep_med_edpi, ep_hgh_edpi;
/* timer to update telemetry every 500ms */
static struct rte_timer telemetry_timer;

/* stats index returned by metrics lib */
int telstats_index;

struct telstats_name {
	char name[RTE_ETH_XSTATS_NAME_SIZE];
};

/* telemetry stats to be reported */
const struct telstats_name telstats_strings[] = {
	{"empty_poll"},
	{"full_poll"},
	{"busy_percent"}
};

/* core busyness in percentage */
enum busy_rate {
	ZERO = 0,
	PARTIAL = 50,
	FULL = 100
};

/* reference poll count to measure core busyness */
#define DEFAULT_COUNT 10000
/*
 * reference CYCLES to be used to
 * measure core busyness based on poll count
 */
#define MIN_CYCLES  1500000ULL
#define MAX_CYCLES 22000000ULL

/* (500ms) */
#define TELEMETRY_INTERVALS_PER_SEC 2

static int parse_ptype; /**< Parse packet type using rx callback, and */
			/**< disabled by default */

enum appmode {
	APP_MODE_DEFAULT = 0,
	APP_MODE_LEGACY,
	APP_MODE_EMPTY_POLL,
	APP_MODE_TELEMETRY,
	APP_MODE_INTERRUPT
};

enum appmode app_mode;

enum freq_scale_hint_t
{
	FREQ_LOWER    =      -1,
	FREQ_CURRENT  =       0,
	FREQ_HIGHER   =       1,
	FREQ_HIGHEST  =       2
};

struct lcore_rx_queue {
	uint16_t port_id;
	uint8_t queue_id;
	enum freq_scale_hint_t freq_up_hint;
	uint32_t zero_rx_packet_count;
	uint32_t idle_hint;
} __rte_cache_aligned;

#define MAX_RX_QUEUE_PER_LCORE 16
#define MAX_TX_QUEUE_PER_PORT RTE_MAX_ETHPORTS
#define MAX_RX_QUEUE_PER_PORT 128

#define MAX_RX_QUEUE_INTERRUPT_PER_PORT 16

struct lcore_params lcore_params_array[MAX_LCORE_PARAMS];
static struct lcore_params lcore_params_array_default[] = {
	{0, 0, 2},
	{0, 1, 2},
	{0, 2, 2},
	{1, 0, 2},
	{1, 1, 2},
	{1, 2, 2},
	{2, 0, 2},
	{3, 0, 3},
	{3, 1, 3},
};

struct lcore_params *lcore_params = lcore_params_array_default;
uint16_t nb_lcore_params = RTE_DIM(lcore_params_array_default);

static struct rte_eth_conf port_conf = {
	.rxmode = {
		.split_hdr_size = 0,
		.max_rx_pkt_len = 256,
		//.offloads =DEV_RX_OFFLOAD_UDP_CKSUM ,
	},
	.txmode = {
		.mq_mode = ETH_MQ_TX_NONE,
		//.offloads=DEV_TX_OFFLOAD_MBUF_FAST_FREE,
	},
};

static struct rte_mempool *pktmbuf_pool[NB_SOCKETS];

#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)

#ifdef RTE_ARCH_X86
#include <rte_hash_crc.h>
#define DEFAULT_HASH_FUNC       rte_hash_crc
#else
#include <rte_jhash.h>
#define DEFAULT_HASH_FUNC       rte_jhash
#endif

struct ipv4_5tuple {
	uint32_t ip_dst;
	uint32_t ip_src;
	uint16_t port_dst;
	uint16_t port_src;
	uint8_t  proto;
} __rte_packed;

struct ipv6_5tuple {
	uint8_t  ip_dst[IPV6_ADDR_LEN];
	uint8_t  ip_src[IPV6_ADDR_LEN];
	uint16_t port_dst;
	uint16_t port_src;
	uint8_t  proto;
} __rte_packed;

struct ipv4_l3fwd_route {
	struct ipv4_5tuple key;
	uint8_t if_out;
};

struct ipv6_l3fwd_route {
	struct ipv6_5tuple key;
	uint8_t if_out;
};

static struct ipv4_l3fwd_route ipv4_l3fwd_route_array[] = {
	{{RTE_IPV4(100,10,0,1), RTE_IPV4(200,10,0,1), 101, 11, IPPROTO_TCP}, 0},
	{{RTE_IPV4(100,20,0,2), RTE_IPV4(200,20,0,2), 102, 12, IPPROTO_TCP}, 1},
	{{RTE_IPV4(100,30,0,3), RTE_IPV4(200,30,0,3), 103, 13, IPPROTO_TCP}, 2},
	{{RTE_IPV4(100,40,0,4), RTE_IPV4(200,40,0,4), 104, 14, IPPROTO_TCP}, 3},
};

static struct ipv6_l3fwd_route ipv6_l3fwd_route_array[] = {
	{
		{
			{0xfe, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
			 0x02, 0x1b, 0x21, 0xff, 0xfe, 0x91, 0x38, 0x05},
			{0xfe, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
			 0x02, 0x1e, 0x67, 0xff, 0xfe, 0x0d, 0xb6, 0x0a},
			 1, 10, IPPROTO_UDP
		}, 4
	},
};

typedef struct rte_hash lookup_struct_t;
static lookup_struct_t *ipv4_l3fwd_lookup_struct[NB_SOCKETS];
static lookup_struct_t *ipv6_l3fwd_lookup_struct[NB_SOCKETS];

#define L3FWD_HASH_ENTRIES 1024

static uint16_t ipv4_l3fwd_out_if[L3FWD_HASH_ENTRIES] __rte_cache_aligned;
static uint16_t ipv6_l3fwd_out_if[L3FWD_HASH_ENTRIES] __rte_cache_aligned;
#endif

#if (APP_LOOKUP_METHOD == APP_LOOKUP_LPM)
struct ipv4_l3fwd_route {
	uint32_t ip;
	uint8_t  depth;
	uint8_t  if_out;
};

static struct ipv4_l3fwd_route ipv4_l3fwd_route_array[] = {
	{RTE_IPV4(1,1,1,0), 24, 0},
	{RTE_IPV4(2,1,1,0), 24, 1},
	{RTE_IPV4(3,1,1,0), 24, 2},
	{RTE_IPV4(4,1,1,0), 24, 3},
	{RTE_IPV4(5,1,1,0), 24, 4},
	{RTE_IPV4(6,1,1,0), 24, 5},
	{RTE_IPV4(7,1,1,0), 24, 6},
	{RTE_IPV4(8,1,1,0), 24, 7},
};

#define IPV4_L3FWD_LPM_MAX_RULES     1024

typedef struct rte_lpm lookup_struct_t;
static lookup_struct_t *ipv4_l3fwd_lookup_struct[NB_SOCKETS];
#endif

struct lcore_conf {
	uint16_t n_rx_queue;
	struct lcore_rx_queue rx_queue_list[MAX_RX_QUEUE_PER_LCORE];
	uint16_t n_tx_port;
	uint16_t tx_port_id[RTE_MAX_ETHPORTS];
	uint16_t tx_queue_id[RTE_MAX_ETHPORTS];
	struct rte_eth_dev_tx_buffer *tx_buffer[RTE_MAX_ETHPORTS];
	lookup_struct_t *ipv4_lookup_struct;
	lookup_struct_t *ipv6_lookup_struct;
} __rte_cache_aligned;

struct lcore_stats {
	/* total sleep time in ms since last frequency scaling down */
	uint32_t sleep_time;
	/* number of long sleep recently */
	uint32_t nb_long_sleep;
	/* freq. scaling up trend */
	uint32_t trend;
	/* total packet processed recently */
	uint64_t nb_rx_processed;
	/* total iterations looped recently */
	uint64_t nb_iteration_looped;
	/*
	 * Represents empty and non empty polls
	 * of rte_eth_rx_burst();
	 * ep_nep[0] holds non empty polls
	 * i.e. 0 < nb_rx <= MAX_BURST
	 * ep_nep[1] holds empty polls.
	 * i.e. nb_rx == 0
	 */
	uint64_t ep_nep[2];
	/*
	 * Represents full and empty+partial
	 * polls of rte_eth_rx_burst();
	 * ep_nep[0] holds empty+partial polls.
	 * i.e. 0 <= nb_rx < MAX_BURST
	 * ep_nep[1] holds full polls
	 * i.e. nb_rx == MAX_BURST
	 */
	uint64_t fp_nfp[2];
	enum busy_rate br;
	rte_spinlock_t telemetry_lock;
} __rte_cache_aligned;

static struct lcore_conf lcore_conf[RTE_MAX_LCORE] __rte_cache_aligned;
static struct lcore_stats stats[RTE_MAX_LCORE] __rte_cache_aligned;
static struct rte_timer power_timers[RTE_MAX_LCORE];

/* Power Mng Algortimgn heriotic: idle-sleep heuristic and queue-based frequency scale-up heuristic are the main per-poll control rules used by the legacy loop. */
static inline uint32_t power_idle_heuristic(uint32_t zero_rx_packet_count);
static inline enum freq_scale_hint_t power_freq_scaleup_heuristic(
		unsigned int lcore_id, uint16_t port_id, uint16_t queue_id);

/*
 * These defaults are using the max frequency index (1), a medium index (9)
 * and a typical low frequency index (14). These can be adjusted to use
 * different indexes using the relevant command line parameters.
 */
static uint8_t freq_tlb[] = {14, 9, 1};

static int is_done(void)
{
	return quit_signal;
}

/* exit signal handler */
static void
signal_exit_now(int sigtype)
{
	if (sigtype == SIGINT)
		quit_signal = true;
}

/*  Freqency scale down timer callback */
/* Power Mng Algortimgn heriotic: periodic timer callback that applies conservative frequency down-scaling based on recent sleep ratio and average packets per loop iteration. */
static void
power_timer_cb(__rte_unused struct rte_timer *tim,
	       __rte_unused void *arg)
{
	uint64_t hz;
	float sleep_time_ratio;
	unsigned lcore_id = rte_lcore_id();

	/* accumulate total execution time in us when callback is invoked */
	sleep_time_ratio = (float)(stats[lcore_id].sleep_time) /
				(float)SCALING_PERIOD;
	/**
	 * check whether need to scale down frequency a step if it sleep a lot.
	 */
	if (sleep_time_ratio >= SCALING_DOWN_TIME_RATIO_THRESHOLD) {
		if (rte_power_freq_down)
			rte_power_freq_down(lcore_id);
	} else if ((unsigned)(stats[lcore_id].nb_rx_processed /
		stats[lcore_id].nb_iteration_looped) < MAX_PKT_BURST) {
		/**
		 * scale down a step if average packet per iteration less
		 * than expectation.
		 */
		if (rte_power_freq_down)
			rte_power_freq_down(lcore_id);
	}

	/**
	 * initialize another timer according to current frequency to ensure
	 * timer interval is relatively fixed.
	 */
	hz = rte_get_timer_hz();
	rte_timer_reset(&power_timers[lcore_id], hz / TIMER_NUMBER_PER_SECOND,
			SINGLE, lcore_id, power_timer_cb, NULL);

	stats[lcore_id].nb_rx_processed = 0;
	stats[lcore_id].nb_iteration_looped = 0;

	stats[lcore_id].sleep_time = 0;
}

//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
#define MIN_ZERO_POLL_COUNT 50000

#define MINIMUM_SLEEP_TIME         0
#define SUSPEND_THRESHOLD          30000

/* Power Mng Algortimgn heriotic: convert long runs of zero-packet polls into an idle hint that decides whether the core keeps spinning or prepares for deeper idle handling. */
static inline uint32_t
power_idle_heuristic(uint32_t zero_rx_packet_count)
{
	/* If zero count is less than 30000,  sleep 5us */
	if (zero_rx_packet_count < SUSPEND_THRESHOLD)
		return MINIMUM_SLEEP_TIME;
	else
		return SUSPEND_THRESHOLD;
}

	/* If zero count is less than 1000, sleep 100 us which is the
		minimum latency switching from C3/C6 to C0
	*/

// static inline uint32_t
// power_idle_heuristic(uint32_t zero_rx_packet_count)
// {
// 	/* If zero count is less than 100,  sleep 1us */
// 	// if (zero_rx_packet_count < 100)
// 	if (zero_rx_packet_count < 100)
// 		return MINIMUM_SLEEP_TIME;
// 	/* If zero count is less than 1000, sleep 100 us which is the
// 		minimum latency switching from C3/C6 to C0
// 	*/
// 	// else if (zero_rx_packet_count < 1000)
// 	else if (zero_rx_packet_count < 1000)
// 		return MINIMUM_SLEEP_TIME2;

// 	else
// 		return SUSPEND_THRESHOLD;
// }

/* Power Mng Algortimgn heriotic: infer how aggressively to raise CPU frequency from instantaneous RX queue occupancy and a short trend accumulator. */
static inline enum freq_scale_hint_t
power_freq_scaleup_heuristic(unsigned lcore_id,
			     uint16_t port_id,
			     uint16_t queue_id)
{
	uint32_t rxq_count = rte_eth_rx_queue_count(port_id, queue_id);
	// if(rxq_count!=0)
	// 	RTE_LOG(INFO, L3FWD_POWER,
	// 				"rte_eth_rx_queue_count %u \n",
	// 				rxq_count);
/**
 * HW Rx queue size is 128 by default, Rx burst read at maximum 32 entries
 * per iteration
 */
#define FREQ_GEAR1_RX_PACKET_THRESHOLD             MAX_PKT_BURST
#define FREQ_GEAR2_RX_PACKET_THRESHOLD             (MAX_PKT_BURST * 2)
#define FREQ_GEAR3_RX_PACKET_THRESHOLD             (MAX_PKT_BURST * 3)

// #define FREQ_UP_TREND1_ACC   1
// #define FREQ_UP_TREND2_ACC   100
// #define FREQ_UP_THRESHOLD    10000
#define FREQ_UP_TREND1_ACC   3
#define FREQ_UP_TREND2_ACC   25
#define FREQ_UP_THRESHOLD    100

	if (likely(rxq_count > FREQ_GEAR3_RX_PACKET_THRESHOLD)) {
		stats[lcore_id].trend = 0;
		return FREQ_HIGHEST;
	} else if (likely(rxq_count > FREQ_GEAR2_RX_PACKET_THRESHOLD))
		stats[lcore_id].trend += FREQ_UP_TREND2_ACC;
	else if (likely(rxq_count > FREQ_GEAR1_RX_PACKET_THRESHOLD))
		stats[lcore_id].trend += FREQ_UP_TREND1_ACC;

	if (likely(stats[lcore_id].trend > FREQ_UP_THRESHOLD)) {
		stats[lcore_id].trend = 0;
		return FREQ_HIGHER;
	}

	return FREQ_CURRENT;
}

/**
 * force polling thread sleep until one-shot rx interrupt triggers
 * @param port_id
 *  Port id.
 * @param queue_id
 *  Rx queue id.
 * @return
 *  0 on success
 */
static int
sleep_until_rx_interrupt(int num)
{
	/*
	 * we want to track when we are woken up by traffic so that we can go
	 * back to sleep again without log spamming.
	 */
	static bool timeout;
	struct rte_epoll_event event[num];
	int n, i;
	uint16_t port_id;
	uint8_t queue_id;
	void *data;

	if (!timeout) {
		RTE_LOG(INFO, L3FWD_POWER,
			"lcore %u sleeps until interrupt triggers\n",
			rte_lcore_id());
	}

	n = rte_epoll_wait(RTE_EPOLL_PER_THREAD, event, num, 50);

	for (i = 0; i < n; i++) {
		data = event[i].epdata.data;
		port_id = ((uintptr_t)data) >> CHAR_BIT;
		queue_id = ((uintptr_t)data) &
			RTE_LEN2MASK(CHAR_BIT, uint8_t);
		RTE_LOG(INFO, L3FWD_POWER,
			"lcore %u is waked up from rx interrupt on"
			" port %d queue %d\n",
			rte_lcore_id(), port_id, queue_id);
	}
	timeout = n == 0;

	return 0;
}

static void
turn_on_off_intr(struct lcore_conf *qconf, bool on)
{
	int i;
	struct lcore_rx_queue *rx_queue;
	uint8_t queue_id;
	uint16_t port_id;

	for (i = 0; i < qconf->n_rx_queue; ++i) {
		rx_queue = &(qconf->rx_queue_list[i]);
		port_id = rx_queue->port_id;
		queue_id = rx_queue->queue_id;

		rte_spinlock_lock(&(locks[port_id]));
		if (on)
			rte_eth_dev_rx_intr_enable(port_id, queue_id);
		else
			rte_eth_dev_rx_intr_disable(port_id, queue_id);
		rte_spinlock_unlock(&(locks[port_id]));
	}
}

static int
event_register(struct lcore_conf *qconf)
{
	struct lcore_rx_queue *rx_queue;
	uint8_t queueid;
	uint16_t portid;
	uint32_t data;
	int ret;
	int i;

	for (i = 0; i < qconf->n_rx_queue; ++i) {
		rx_queue = &(qconf->rx_queue_list[i]);
		portid = rx_queue->port_id;
		queueid = rx_queue->queue_id;
		data = portid << CHAR_BIT | queueid;

		ret = rte_eth_dev_rx_intr_ctl_q(portid, queueid,
						RTE_EPOLL_PER_THREAD,
						RTE_INTR_EVENT_ADD,
						(void *)((uintptr_t)data));
		if (ret)
			return ret;
	}

	return 0;
}

/* Enqueue a single packet, and send burst if queue is filled */
static inline int
send_single_packet(struct rte_mbuf *m, uint16_t port)
{
	uint32_t lcore_id;
	struct lcore_conf *qconf;

	lcore_id = rte_lcore_id();
	qconf = &lcore_conf[lcore_id];

	rte_eth_tx_buffer(port, qconf->tx_queue_id[port],
			qconf->tx_buffer[port], m);

	return 0;
}

#ifdef DO_RFC_1812_CHECKS
static inline int
is_valid_ipv4_pkt(struct rte_ipv4_hdr *pkt, uint32_t link_len)
{
	/* From http://www.rfc-editor.org/rfc/rfc1812.txt section 5.2.2 */
	/*
	 * 1. The packet length reported by the Link Layer must be large
	 * enough to hold the minimum length legal IP datagram (20 bytes).
	 */
	if (link_len < sizeof(struct rte_ipv4_hdr))
		return -1;

	/* 2. The IP checksum must be correct. */
	/* this is checked in H/W */

	/*
	 * 3. The IP version number must be 4. If the version number is not 4
	 * then the packet may be another version of IP, such as IPng or
	 * ST-II.
	 */
	if (((pkt->version_ihl) >> 4) != 4)
		return -3;
	/*
	 * 4. The IP header length field must be large enough to hold the
	 * minimum length legal IP datagram (20 bytes = 5 words).
	 */
	if ((pkt->version_ihl & 0xf) < 5)
		return -4;

	/*
	 * 5. The IP total length field must be large enough to hold the IP
	 * datagram header, whose length is specified in the IP header length
	 * field.
	 */
	if (rte_cpu_to_be_16(pkt->total_length) < sizeof(struct rte_ipv4_hdr))
		return -5;

	return 0;
}
#endif

#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
static void
print_ipv4_key(struct ipv4_5tuple key)
{
	printf("IP dst = %08x, IP src = %08x, port dst = %d, port src = %d, "
		"proto = %d\n", (unsigned)key.ip_dst, (unsigned)key.ip_src,
		key.port_dst, key.port_src, key.proto);
}
static void
print_ipv6_key(struct ipv6_5tuple key)
{
	printf("IP dst = " IPv6_BYTES_FMT ", IP src = " IPv6_BYTES_FMT ", "
	       "port dst = %d, port src = %d, proto = %d\n",
	       IPv6_BYTES(key.ip_dst), IPv6_BYTES(key.ip_src),
	       key.port_dst, key.port_src, key.proto);
}

static inline uint16_t
get_ipv4_dst_port(struct rte_ipv4_hdr *ipv4_hdr, uint16_t portid,
		lookup_struct_t *ipv4_l3fwd_lookup_struct)
{
	struct ipv4_5tuple key;
	struct rte_tcp_hdr *tcp;
	struct rte_udp_hdr *udp;
	int ret = 0;

	key.ip_dst = rte_be_to_cpu_32(ipv4_hdr->dst_addr);
	key.ip_src = rte_be_to_cpu_32(ipv4_hdr->src_addr);
	key.proto = ipv4_hdr->next_proto_id;

	switch (ipv4_hdr->next_proto_id) {
	case IPPROTO_TCP:
		tcp = (struct rte_tcp_hdr *)((unsigned char *)ipv4_hdr +
					sizeof(struct rte_ipv4_hdr));
		key.port_dst = rte_be_to_cpu_16(tcp->dst_port);
		key.port_src = rte_be_to_cpu_16(tcp->src_port);
		break;

	case IPPROTO_UDP:
		udp = (struct rte_udp_hdr *)((unsigned char *)ipv4_hdr +
					sizeof(struct rte_ipv4_hdr));
		key.port_dst = rte_be_to_cpu_16(udp->dst_port);
		key.port_src = rte_be_to_cpu_16(udp->src_port);
		break;

	default:
		key.port_dst = 0;
		key.port_src = 0;
		break;
	}

	/* Find destination port */
	ret = rte_hash_lookup(ipv4_l3fwd_lookup_struct, (const void *)&key);
	return ((ret < 0) ? portid : ipv4_l3fwd_out_if[ret]);
}

static inline uint16_t
get_ipv6_dst_port(struct rte_ipv6_hdr *ipv6_hdr, uint16_t portid,
		lookup_struct_t *ipv6_l3fwd_lookup_struct)
{
	struct ipv6_5tuple key;
	struct rte_tcp_hdr *tcp;
	struct rte_udp_hdr *udp;
	int ret = 0;

	memcpy(key.ip_dst, ipv6_hdr->dst_addr, IPV6_ADDR_LEN);
	memcpy(key.ip_src, ipv6_hdr->src_addr, IPV6_ADDR_LEN);

	key.proto = ipv6_hdr->proto;

	switch (ipv6_hdr->proto) {
	case IPPROTO_TCP:
		tcp = (struct rte_tcp_hdr *)((unsigned char *)ipv6_hdr +
					sizeof(struct rte_ipv6_hdr));
		key.port_dst = rte_be_to_cpu_16(tcp->dst_port);
		key.port_src = rte_be_to_cpu_16(tcp->src_port);
		break;

	case IPPROTO_UDP:
		udp = (struct rte_udp_hdr *)((unsigned char *)ipv6_hdr +
					sizeof(struct rte_ipv6_hdr));
		key.port_dst = rte_be_to_cpu_16(udp->dst_port);
		key.port_src = rte_be_to_cpu_16(udp->src_port);
		break;

	default:
		key.port_dst = 0;
		key.port_src = 0;
		break;
	}

	/* Find destination port */
	ret = rte_hash_lookup(ipv6_l3fwd_lookup_struct, (const void *)&key);
	return ((ret < 0) ? portid : ipv6_l3fwd_out_if[ret]);
}
#endif

#if (APP_LOOKUP_METHOD == APP_LOOKUP_LPM)
static inline uint16_t
get_ipv4_dst_port(struct rte_ipv4_hdr *ipv4_hdr, uint16_t portid,
		lookup_struct_t *ipv4_l3fwd_lookup_struct)
{
	uint32_t next_hop;

	return ((rte_lpm_lookup(ipv4_l3fwd_lookup_struct,
			rte_be_to_cpu_32(ipv4_hdr->dst_addr), &next_hop) == 0) ?
			next_hop : portid);
}
#endif

static inline void
parse_ptype_one(struct rte_mbuf *m)
{
	struct rte_ether_hdr *eth_hdr;
	uint32_t packet_type = RTE_PTYPE_UNKNOWN;
	uint16_t ether_type;

	eth_hdr = rte_pktmbuf_mtod(m, struct rte_ether_hdr *);
	ether_type = eth_hdr->ether_type;
	if (ether_type == rte_cpu_to_be_16(RTE_ETHER_TYPE_IPV4))
		packet_type |= RTE_PTYPE_L3_IPV4_EXT_UNKNOWN;
	else if (ether_type == rte_cpu_to_be_16(RTE_ETHER_TYPE_IPV6))
		packet_type |= RTE_PTYPE_L3_IPV6_EXT_UNKNOWN;

	m->packet_type = packet_type;
}

static uint16_t
cb_parse_ptype(uint16_t port __rte_unused, uint16_t queue __rte_unused,
	       struct rte_mbuf *pkts[], uint16_t nb_pkts,
	       uint16_t max_pkts __rte_unused,
	       void *user_param __rte_unused)
{
	unsigned int i;

	for (i = 0; i < nb_pkts; ++i)
		parse_ptype_one(pkts[i]);

	return nb_pkts;
}

static int
add_cb_parse_ptype(uint16_t portid, uint16_t queueid)
{
	printf("Port %d: softly parse packet type info\n", portid);
	if (rte_eth_add_rx_callback(portid, queueid, cb_parse_ptype, NULL))
		return 0;

	printf("Failed to add rx callback: port=%d\n", portid);
	return -1;
}

static inline void
l3fwd_simple_forward(struct rte_mbuf *m, uint16_t portid, struct lcore_conf *qconf)
{
	struct rte_ether_hdr *eth_hdr;
	struct rte_ipv4_hdr *ipv4_hdr;
	void *d_addr_bytes;
	uint16_t dst_port;

	eth_hdr = rte_pktmbuf_mtod(m, struct rte_ether_hdr *);

	if (RTE_ETH_IS_IPV4_HDR(m->packet_type)) {
		/* Handle IPv4 headers.*/
		ipv4_hdr =
			rte_pktmbuf_mtod_offset(m, struct rte_ipv4_hdr *,
						sizeof(struct rte_ether_hdr));

#ifdef DO_RFC_1812_CHECKS
		/* Check to make sure the packet is valid (RFC1812) */
		if (is_valid_ipv4_pkt(ipv4_hdr, m->pkt_len) < 0) {
			rte_pktmbuf_free(m);
			return;
		}
#endif

		dst_port = get_ipv4_dst_port(ipv4_hdr, portid,
					qconf->ipv4_lookup_struct);
		if (dst_port >= RTE_MAX_ETHPORTS ||
				(enabled_port_mask & 1 << dst_port) == 0)
			dst_port = portid;

		/* 02:00:00:00:00:xx */
		d_addr_bytes = &eth_hdr->d_addr.addr_bytes[0];
		*((uint64_t *)d_addr_bytes) =
			0x000000000002 + ((uint64_t)dst_port << 40);

#ifdef DO_RFC_1812_CHECKS
		/* Update time to live and header checksum */
		--(ipv4_hdr->time_to_live);
		++(ipv4_hdr->hdr_checksum);
#endif

		/* src addr */
		rte_ether_addr_copy(&ports_eth_addr[dst_port],
				&eth_hdr->s_addr);

		send_single_packet(m, dst_port);
	} else if (RTE_ETH_IS_IPV6_HDR(m->packet_type)) {
		/* Handle IPv6 headers.*/
#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
		struct rte_ipv6_hdr *ipv6_hdr;

		ipv6_hdr =
			rte_pktmbuf_mtod_offset(m, struct rte_ipv6_hdr *,
						sizeof(struct rte_ether_hdr));

		dst_port = get_ipv6_dst_port(ipv6_hdr, portid,
					qconf->ipv6_lookup_struct);

		if (dst_port >= RTE_MAX_ETHPORTS ||
				(enabled_port_mask & 1 << dst_port) == 0)
			dst_port = portid;

		/* 02:00:00:00:00:xx */
		d_addr_bytes = &eth_hdr->d_addr.addr_bytes[0];
		*((uint64_t *)d_addr_bytes) =
			0x000000000002 + ((uint64_t)dst_port << 40);

		/* src addr */
		rte_ether_addr_copy(&ports_eth_addr[dst_port],
				&eth_hdr->s_addr);

		send_single_packet(m, dst_port);
#else
		/* We don't currently handle IPv6 packets in LPM mode. */
		rte_pktmbuf_free(m);
#endif
	} else
		rte_pktmbuf_free(m);
}

static void
l2fwd_mac_updating(struct rte_mbuf *m, unsigned dest_portid)
{
	struct rte_ether_hdr *eth;
	struct rte_ether_addr tmp_mac;

	RTE_SET_USED(dest_portid);

	eth = rte_pktmbuf_mtod(m, struct rte_ether_hdr *);

	/* Swap source and destination MAC addresses */
	rte_ether_addr_copy(&eth->s_addr, &tmp_mac);
	rte_ether_addr_copy(&eth->d_addr, &eth->s_addr);
	rte_ether_addr_copy(&tmp_mac, &eth->d_addr);

	/* Set new source MAC address */
	// rte_ether_addr_copy(&L3FWD_POWER_ports_eth_addr[dest_portid], &eth->s_addr);
}

static void
l2fwd_simple_forward(struct rte_mbuf *m, uint16_t portid)
{
	// unsigned dst_port;
	// int sent;
	// struct rte_eth_dev_tx_buffer *buffer;

	//dst_port = L3FWD_POWER_dst_ports[portid];
	//dst_port=16;
	//if (mac_updating)
	l2fwd_mac_updating(m, portid);

	uint32_t lcore_id;
	struct lcore_conf *qconf1;

	lcore_id = rte_lcore_id();
	qconf1 = &lcore_conf[lcore_id];

	rte_eth_tx_buffer(portid, 0, qconf1->tx_buffer[portid], m);
}

//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-

void
gtp_decapsulate(struct rte_mbuf *m, uint32_t teid)
{
	struct rte_ether_hdr eth_copy;
	struct rte_ipv4_hdr ipv4_copy;

	struct rte_ether_hdr *eth_hdr =
		rte_pktmbuf_mtod(m, struct rte_ether_hdr *);
	struct rte_ipv4_hdr *ipv4_hdr =
		rte_pktmbuf_mtod_offset(m, struct rte_ipv4_hdr *,
			sizeof(struct rte_ether_hdr));

	rte_memcpy(&eth_copy, eth_hdr, sizeof(eth_copy));
	rte_memcpy(&ipv4_copy, ipv4_hdr, sizeof(ipv4_copy));

	/* Calculate the total header length to be removed */
	uint16_t gtp_header_len = sizeof(struct rte_ether_hdr) +
		sizeof(struct rte_ipv4_hdr) +
		sizeof(struct rte_udp_hdr) +
		sizeof(struct gtp_header);

	/* Adjust the packet buffer to remove the GTP header and access the payload */
	if (rte_pktmbuf_adj(m, gtp_header_len) < 0) {
		printf("Failed to decapsulate GTP header\n");
	} else {
		//printf("GTP header decapsulated\n");
	}

	struct sdap_header *sdap_hdr;

	/* Append space for SDAP header */
	sdap_hdr = (struct sdap_header *)rte_pktmbuf_prepend(m, sizeof(struct sdap_header));
	if (sdap_hdr == NULL) {
		printf("Failed to append SDAP header\n");
		return;
	}

	/* Set the SDAP header values (using a default RQI value) */
	sdap_hdr->rdi_rqi_qfi = 0; // Example default RQI value

	struct pdcp_header *pdcp_hdr;

	/* Append space for PDCP header */
	pdcp_hdr = (struct pdcp_header *)rte_pktmbuf_prepend(m, sizeof(struct pdcp_header));
	if (pdcp_hdr == NULL) {
		printf("Failed to append PDCP header\n");
		return;
	}

	/* Set the PDCP header values with default values */
	pdcp_hdr->dc_r = 0; // Default value
	pdcp_hdr->sn = rte_cpu_to_be_16(0); // Default SN value

	struct rlc_ack_mode_header *rlc_hdr;

	/* Append space for RLC ACK mode header */
	rlc_hdr = (struct rlc_ack_mode_header *)rte_pktmbuf_prepend(m, sizeof(struct rlc_ack_mode_header));
	if (rlc_hdr == NULL) {
		printf("Failed to append RLC ACK mode header\n");
		return;
	}

	/* Set the RLC ACK mode header values with default values */
	rlc_hdr->dc_p_si_r = 1; // Default value
	rlc_hdr->sn = 1; // Default SN value
	rlc_hdr->teid = teid; // Default TEID value

	/* Create and prepend the new UDP header */
	struct udp_hdr *udp_hdr = (struct udp_hdr *)rte_pktmbuf_prepend(m, sizeof(struct udp_hdr));
	if (udp_hdr == NULL) {
		printf("Failed to prepend UDP header\n");
		return;
	}
	udp_hdr->src_port = rte_cpu_to_be_16(8043); // Example source port
	udp_hdr->dst_port = 2134; // GTP-U port (2152)
	udp_hdr->dgram_len = 0;

	/* Prepend the original IPv4 header to the packet */
	struct rte_ipv4_hdr *new_ipv4_hdr =
		(struct rte_ipv4_hdr *)rte_pktmbuf_prepend(m, sizeof(struct rte_ipv4_hdr));
	if (new_ipv4_hdr == NULL) {
		printf("Failed to prepend IPv4 header\n");
		return;
	}

	rte_memcpy(new_ipv4_hdr, &ipv4_copy, sizeof(struct rte_ipv4_hdr));

	/* Prepend the original Ethernet header to the packet */
	struct rte_ether_hdr *new_eth_hdr =
		(struct rte_ether_hdr *)rte_pktmbuf_prepend(m, sizeof(struct rte_ether_hdr));
	if (new_eth_hdr == NULL) {
		printf("Failed to prepend Ethernet header\n");
		return;
	}

	rte_memcpy(new_eth_hdr, &eth_copy, sizeof(struct rte_ether_hdr));
}

void
gtp_decapsulate_latency(struct rte_mbuf *m, uint32_t teid)
{
	/* 1) Save original Ethernet header */
	struct rte_ether_hdr *orig_eth_hdr = rte_pktmbuf_mtod(m, struct rte_ether_hdr *);
	struct rte_ether_hdr eth_copy;
	rte_memcpy(&eth_copy, orig_eth_hdr, sizeof(eth_copy));

	/* 2) Strip off the GTP-U outer headers */
	uint16_t gtp_header_len =
		sizeof(struct rte_ether_hdr) +
		sizeof(struct rte_ipv4_hdr) +
		sizeof(struct rte_udp_hdr) +
		sizeof(struct gtp_header);
	if (rte_pktmbuf_adj(m, gtp_header_len) < 0) {
		printf("Failed to decapsulate GTP header\n");
		return;
	}

	/* 3) Prepend SDAP header */
	struct sdap_header *sdap_hdr =
		(struct sdap_header *)rte_pktmbuf_prepend(m, sizeof(struct sdap_header));
	if (sdap_hdr == NULL) { printf("Failed to append SDAP header\n"); return; }
	sdap_hdr->rdi_rqi_qfi = 0;

	/* 4) Prepend PDCP header */
	struct pdcp_header *pdcp_hdr =
		(struct pdcp_header *)rte_pktmbuf_prepend(m, sizeof(struct pdcp_header));
	if (pdcp_hdr == NULL) { printf("Failed to append PDCP header\n"); return; }
	pdcp_hdr->dc_r = 0;
	pdcp_hdr->sn = rte_cpu_to_be_16(0);

	/* 5) Prepend RLC ACK-mode header */
	struct rlc_ack_mode_header *rlc_hdr =
		(struct rlc_ack_mode_header *)rte_pktmbuf_prepend(m, sizeof(struct rlc_ack_mode_header));
	if (rlc_hdr == NULL) { printf("Failed to append RLC ACK mode header\n"); return; }
	rlc_hdr->dc_p_si_r = 1;
	rlc_hdr->sn = 1;
	rlc_hdr->teid = teid;

	/* 6) Prepend new UDP header */
	struct udp_hdr *udp_hdr =
		(struct udp_hdr *)rte_pktmbuf_prepend(m, sizeof(struct udp_hdr));
	if (udp_hdr == NULL) { printf("Failed to prepend UDP header\n"); return; }
	udp_hdr->src_port = rte_cpu_to_be_16(8043);
	udp_hdr->dst_port = rte_cpu_to_be_16(2152);
	udp_hdr->dgram_len = rte_cpu_to_be_16(rte_pktmbuf_pkt_len(m));

	/* 7) Prepend new IPv4 header */
	struct rte_ipv4_hdr *ip_hdr =
		(struct rte_ipv4_hdr *)rte_pktmbuf_prepend(m, sizeof(struct rte_ipv4_hdr));
	if (ip_hdr == NULL) { printf("Failed to prepend IPv4 header\n"); return; }
	ip_hdr->version_ihl = (4 << 4) | (sizeof(struct rte_ipv4_hdr) / 4);
	ip_hdr->type_of_service = 0x1;                                   // TOS LSB=1 for TRex
	ip_hdr->total_length = rte_cpu_to_be_16(rte_pktmbuf_pkt_len(m));
	ip_hdr->packet_id = rte_cpu_to_be_16(0xffff);
	ip_hdr->fragment_offset = 0;
	ip_hdr->time_to_live = 64;
	ip_hdr->next_proto_id = IPPROTO_UDP;
	ip_hdr->hdr_checksum = 0;                                        // HW will fill
	ip_hdr->src_addr = rte_cpu_to_be_32(0xc0a80101);                 // 192.168.1.1
	ip_hdr->dst_addr = rte_cpu_to_be_32(0xc0a80102);                 // 192.168.1.2

	/* 8) Prepend original Ethernet header */
	struct rte_ether_hdr *new_eth_hdr =
		(struct rte_ether_hdr *)rte_pktmbuf_prepend(m, sizeof(struct rte_ether_hdr));
	if (new_eth_hdr == NULL) { printf("Failed to prepend Ethernet header\n"); return; }
	rte_memcpy(new_eth_hdr, &eth_copy, sizeof(eth_copy));
}

void
gtp_encapsulate(struct rte_mbuf *m, uint32_t teid)
{
	struct rte_ether_hdr eth_copy;
	struct rte_ether_hdr *eth_hdr = rte_pktmbuf_mtod(m, struct rte_ether_hdr *);
	rte_memcpy(&eth_copy, eth_hdr, sizeof(eth_copy));

	uint16_t other_header_len = sizeof(struct rte_ether_hdr) +
		sizeof(struct rte_ipv4_hdr) +
		sizeof(struct rte_udp_hdr) +
		sizeof(struct rlc_ack_mode_header) +
		sizeof(struct pdcp_header) +
		sizeof(struct sdap_header);

	if (rte_pktmbuf_adj(m, other_header_len) < 0) {
		printf("Failed to remove other header\n");
	}

	/* Create and prepend the new GTP-U header */
	struct gtp_header *gtp_hdr =
		(struct gtp_header *)rte_pktmbuf_prepend(m, sizeof(struct gtp_header));
	if (gtp_hdr == NULL) {
		printf("Failed to prepend GTP header\n");
		return;
	}
	gtp_hdr->teid = rte_cpu_to_be_32(teid);
	//gtp_hdr->length = rte_cpu_to_be_16();

	/* Create and prepend the new UDP header */
	struct udp_hdr *udp_hdr = (struct udp_hdr *)rte_pktmbuf_prepend(m, sizeof(struct udp_hdr));
	if (udp_hdr == NULL) {
		printf("Failed to prepend UDP header\n");
		return;
	}
	udp_hdr->src_port = rte_cpu_to_be_16(2153); // Example source port
	udp_hdr->dst_port = 2134; // GTP-U port (2152)
	udp_hdr->dgram_len = 0;

	/* Create and prepend the new outer IP header */
	struct ipv4_hdr *ip_hdr = (struct ipv4_hdr *)rte_pktmbuf_prepend(m, sizeof(struct ipv4_hdr));
	if (ip_hdr == NULL) {
		printf("Failed to prepend IP header\n");
		return;
	}
	ip_hdr->version_ihl = (4 << 4) | (sizeof(struct ipv4_hdr) / 4);
	ip_hdr->type_of_service = 0;
	ip_hdr->total_length = rte_cpu_to_be_16(rte_pktmbuf_pkt_len(m));
	ip_hdr->packet_id = rte_cpu_to_be_16(1);
	ip_hdr->fragment_offset = 0;
	ip_hdr->time_to_live = 64;
	ip_hdr->next_proto_id = IPPROTO_UDP; // Indicates that UDP follows
	ip_hdr->hdr_checksum = 0; // Will be calculated by hardware if enabled
	// ip_hdr->src_addr = rte_cpu_to_be_32(0xc0a80101); // Example source IP (192.168.1.1)
	// ip_hdr->dst_addr = rte_cpu_to_be_32(0xc0a80102); // Example destination IP (192.168.1.2)

	/* Calculate IP checksum */
	ip_hdr->hdr_checksum = rte_ipv4_cksum((struct rte_ipv4_hdr *)ip_hdr);

	struct rte_ether_hdr *new_eth_hdr =
		(struct rte_ether_hdr *)rte_pktmbuf_prepend(m, sizeof(struct rte_ether_hdr));
	if (new_eth_hdr == NULL) {
		printf("Failed to prepend Ethernet header\n");
		return;
	}

	/* Copy the original Ethernet header values */
	rte_memcpy(new_eth_hdr, &eth_copy, sizeof(struct rte_ether_hdr));
}

void
swap_mac_addresses(struct rte_ether_hdr *eth_hdr)
{
	struct rte_ether_addr temp;
	rte_ether_addr_copy(&eth_hdr->s_addr, &temp);
	rte_ether_addr_copy(&eth_hdr->d_addr, &eth_hdr->s_addr);
	rte_ether_addr_copy(&temp, &eth_hdr->d_addr);
}

void
set_egress_port(struct rte_mbuf *m, uint16_t port)
{
	m->port = port;
}

void
parse_gtp(struct rte_mbuf *m, struct metadata *meta)
{
	struct gtp_header *gtp_hdr =
		rte_pktmbuf_mtod_offset(m, struct gtp_header *,
			sizeof(struct rte_ether_hdr) + sizeof(struct rte_ipv4_hdr) +
			sizeof(struct rte_udp_hdr));
	meta->teid = rte_be_to_cpu_32(gtp_hdr->teid);
}

void
parse_rlc(struct rte_mbuf *m, struct metadata *meta)
{
	struct rlc_ack_mode_header *rlc_hdr =
		rte_pktmbuf_mtod_offset(m, struct rlc_ack_mode_header *,
			sizeof(struct rte_ether_hdr) + sizeof(struct rte_ipv4_hdr) +
			sizeof(struct rte_udp_hdr) + sizeof(struct gtp_header));
	(void)rlc_hdr;
	(void)meta;
	//meta->rbnumber = rlc_hdr->rbnum;
}

void
parse_pdcp(struct rte_mbuf *m, struct metadata *meta)
{
	struct pdcp_header *pdcp_hdr =
		rte_pktmbuf_mtod_offset(m, struct pdcp_header *,
			sizeof(struct rte_ether_hdr) + sizeof(struct rte_ipv4_hdr) +
			sizeof(struct rte_udp_hdr) + sizeof(struct gtp_header) +
			sizeof(struct rlc_ack_mode_header));
	(void)pdcp_hdr;
	(void)meta;
	// No specific metadata to store from PDCP header for now
}

void
parse_sdap(struct rte_mbuf *m, struct metadata *meta)
{
	struct sdap_header *sdap_hdr =
		rte_pktmbuf_mtod_offset(m, struct sdap_header *,
			sizeof(struct rte_ether_hdr) + sizeof(struct rte_ipv4_hdr) +
			sizeof(struct rte_udp_hdr) + sizeof(struct gtp_header) +
			sizeof(struct rlc_ack_mode_header) + sizeof(struct pdcp_header));
	(void)sdap_hdr;
	(void)meta;
	//meta->rqibool = sdap_hdr->rdi_rqi_qfi & 0x01;
}

uint32_t
calculate_fnv_hash(uint32_t src_ip, uint32_t dst_ip)
{
	uint64_t addr_pair = ((uint64_t)src_ip << 32) | dst_ip;
	uint32_t hash = 2166136261U;
	hash = (hash ^ addr_pair) * 16777219U;
	return hash;
}

// Function to lookup a TEID in teid_qfi_table
void
lookup_teid_in_teid_qfi_table(uint32_t teid)
{
	uint8_t *rbnumber;
	int ret = rte_hash_lookup_data(teid_qfi_table, &teid, (void **)&rbnumber);

	if (ret < 0) {
		printf("Failed to lookup TEID = %u in teid_qfi_table\n", teid);
	} else {
		//printf("Lookup TEID = %u: RB = %u\n", teid, rbnumber);
	}
}

// Function to lookup an IP address in ul_teid_table
void
lookup_ip_in_ul_teid_table(const char *ip_str)
{
	uint32_t ip;
	uint32_t *teid;
	if (inet_pton(AF_INET, ip_str, &ip) != 1) {
		printf("Invalid IP address format: %s\n", ip_str);
		return;
	}

	int ret = rte_hash_lookup_data(ul_teid_table, &ip, (void **)&teid);

	if (ret < 0) {
		printf("Failed to lookup IP = %s in ul_teid_table\n", ip_str);
	} else {
		//printf("Lookup IP = %s: TEID = %u\n", ip_str, teid);
	}
}

// Function to print mbuf details
void
print_mbuf(struct rte_mbuf *m)
{
	// Print mbuf metadata
	printf("mbuf details:\n");
	printf("Packet length: %u\n", rte_pktmbuf_pkt_len(m));
	printf("Data length: %u\n", rte_pktmbuf_data_len(m));
	printf("Buffer length: %u\n", m->buf_len);
	printf("Refcount: %u\n", rte_mbuf_refcnt_read(m));

	// Print packet data as hex
	printf("Packet data (hex):\n");
	uint8_t *data = rte_pktmbuf_mtod(m, uint8_t *);
	for (uint32_t i = 0; i < rte_pktmbuf_data_len(m); i++) {
		printf("%02x ", data[i]);
		if ((i + 1) % 16 == 0) {
			printf("\n");
		}
	}
	printf("\n");

	// Optional: Interpret the packet data as network headers (Ethernet, IP, UDP)
	struct rte_ether_hdr *eth_hdr = rte_pktmbuf_mtod(m, struct rte_ether_hdr *);
	printf("Ethernet Header:\n");
	printf("  Src MAC: %02X:%02X:%02X:%02X:%02X:%02X\n",
	       eth_hdr->s_addr.addr_bytes[0], eth_hdr->s_addr.addr_bytes[1],
	       eth_hdr->s_addr.addr_bytes[2], eth_hdr->s_addr.addr_bytes[3],
	       eth_hdr->s_addr.addr_bytes[4], eth_hdr->s_addr.addr_bytes[5]);
	printf("  Dst MAC: %02X:%02X:%02X:%02X:%02X:%02X\n",
	       eth_hdr->d_addr.addr_bytes[0], eth_hdr->d_addr.addr_bytes[1],
	       eth_hdr->d_addr.addr_bytes[2], eth_hdr->d_addr.addr_bytes[3],
	       eth_hdr->d_addr.addr_bytes[4], eth_hdr->d_addr.addr_bytes[5]);
	printf("  EtherType: 0x%04X\n", rte_be_to_cpu_16(eth_hdr->ether_type));
}

//static int clone_idx = 0;
// Array to store per-core clone indices
static unsigned clone_idx[MAX_PORTS] = {0};

void
buffer_packet(struct rte_mbuf *m, unsigned portid, int x)
{
	// Get the current lcore ID
	unsigned lcore_id = rte_lcore_id();
	unsigned lcore_index = rte_lcore_index(lcore_id);
	// lcore_index=lcore_index-1;****

	// Fetch a pre-allocated mbuf from the clone pool
	struct rte_mbuf *deep_copy_pkt = clone_mbufs[lcore_index][clone_idx[lcore_index]];
	clone_idx[lcore_index] = (clone_idx[lcore_index] + 1) % CLONE_POOL_SIZE;

	if (deep_copy_pkt == NULL) {
		printf("Failed to allocate new mbuf for deep copy\n");
		return;
	}

	// Set the length of the new mbuf to match the original
	deep_copy_pkt->pkt_len = m->pkt_len;
	deep_copy_pkt->data_len = m->data_len;

	// Copy the packet data from the original mbuf to the new mbuf
	rte_memcpy(rte_pktmbuf_mtod(deep_copy_pkt, void *),
		   rte_pktmbuf_mtod(m, void *),
		   m->pkt_len);

	// Ensure the deep copy has the correct mbuf headroom
	deep_copy_pkt->data_off = m->data_off;

	// Verify the deep copy
	if (deep_copy_pkt->pkt_len != m->pkt_len || deep_copy_pkt->data_len != m->data_len) {
		printf("Mismatch in packet length after copying\n");
		rte_pktmbuf_free(deep_copy_pkt);
		return;
	} else {
		uint8_t *pkt_data = rte_pktmbuf_mtod(deep_copy_pkt, uint8_t *);
		uint32_t teid = rte_be_to_cpu_32(*(uint32_t *)(pkt_data + 14 + 20 + 8 + x));

		struct packet_in_buffer_t **pointer_to_packet_in_bucket;
		(void)pointer_to_packet_in_bucket;

		int ret1 = rte_hash_add_key_data(buffer_table, &teid, (void *)deep_copy_pkt);
		if (ret1 == 22) {
			RTE_LOG(INFO, L3FWD_POWER, "ERROR INSERTION: %d WRONG PARAM\n", ret1);
			rte_exit(EXIT_FAILURE, "UNABLE TO STORE HASH ENTRY WRONG PARAM\n");
		} else if (ret1 == ENOSPC) {
			RTE_LOG(INFO, L3FWD_POWER, "ERROR INSERTION: %d NO SPACE\n", ret1);
			rte_exit(EXIT_FAILURE, "UNABLE TO STORE HASH ENTRY NO SPACE\n");
		} else {
			//port_statistics[portid].inserted += 1;
			//RTE_LOG(INFO, L3FWD_POWER, "NOTE, insertion ok\n");
		}
	}

	(void)portid;
}

//void handle_packet(struct rte_mbuf *m,struct rte_ring *send_ringx,unsigned portid,int clone_idx) {
void
handle_packet(struct rte_mbuf *m, unsigned portid)
{
	struct metadata meta;
	uint8_t *rbnumber;
	uint32_t *lookedteid;
	(void)lookedteid;

	uint8_t *pkt_data = rte_pktmbuf_mtod(m, uint8_t *);
	uint16_t src_udp_port = rte_be_to_cpu_16(*(uint16_t *)(pkt_data + 14 + 20));

	if (src_udp_port == UDP_PORT_HOST_GTP) {
		meta.teid = rte_be_to_cpu_32(*(uint32_t *)(pkt_data + 14 + 20 + 8 + 4));

		int ret = rte_hash_lookup_data(teid_qfi_table, &meta.teid, (void **)&rbnumber);
		if (ret >= 0) {
			if (rbnumber == 0) {
				if (meta.teid == 63999) {
					buffer_packet(m, portid, 4);
					gtp_decapsulate_latency(m, meta.teid);
				} else {
					buffer_packet(m, portid, 4);
					gtp_decapsulate(m, meta.teid);
				}
			} else if (rbnumber == (uint8_t *)1) {
				gtp_decapsulate(m, meta.teid);
				buffer_packet(m, portid, 4);
			} else {
				printf("gtp rbnumber %p is invalid\n", (void *)rbnumber);
				rte_pktmbuf_free(m);
			}
		} else {
			printf("TEID %u not found in hash table\n", meta.teid);
			rte_pktmbuf_free(m);
		}
	} else if (src_udp_port == UDP_PORT_HOST_RLC) {
		/* we did header proc for this pkts in netronome donot do any thing only clone packet */
	} else if (src_udp_port == UDP_SPORT_RLC) {
	}
}

#define BUFFER_PERCENTAGE 10
#define CLEANUP_INTERVAL_S 5  // Cleanup every x seconds
uint64_t total_packets_del;
static void
cleanup_buffer_table(lookup_struct_t *buffer_table, unsigned portid)
{
	const void *key;
	void *data;
	uint32_t next = 0;

	while ((rte_hash_iterate(buffer_table, &key, &data, &next)) >= 0) {
		struct rte_mbuf *buffered_pkt = (struct rte_mbuf *)data;

		// Free the mbuf
		rte_pktmbuf_free(buffered_pkt);

		// Remove the key from the hash table
		rte_hash_del_key(buffer_table, key);

		// Update statistics
		total_packets_del++;
		RTE_LOG(INFO, L3FWD_POWER, "teid deleted 0x%.16" PRIX32 "\n",
			*(const uint32_t *)key);
	}

	(void)portid;
	//printf("Cleanup completed. All buffered packets are freed.\n");
}

void
populate_teid_qfi_table(const char *filename)
{
	json_t *root;
	json_error_t error;

	root = json_load_file(filename, 0, &error);
	if (!root) {
		fprintf(stderr, "Error: on line %d: %s\n", error.line, error.text);
		return;
	}

	json_t *tables = json_object_get(root, "tables");
	if (!json_is_object(tables)) {
		fprintf(stderr, "Error: 'tables' is not an object\n");
		json_decref(root);
		return;
	}

	json_t *teid_qfi_table_json = json_object_get(tables, "ingress::TEIDs_QFIs_to_RBs_RQI");
	if (!json_is_object(teid_qfi_table_json)) {
		fprintf(stderr, "Error: 'ingress::TEIDs_QFIs_to_RBs_RQI' is not an object\n");
		json_decref(root);
		return;
	}

	json_t *rules_array = json_object_get(teid_qfi_table_json, "rules");
	if (!json_is_array(rules_array)) {
		fprintf(stderr, "Error: 'rules' is not an array\n");
		json_decref(root);
		return;
	}

	struct rte_hash_parameters hash_params = {
		.name = HASH_TABLE_NAME,
		.entries = HASH_TABLE_SIZE,
		.key_len = sizeof(uint32_t),
		.hash_func = rte_jhash,
		.hash_func_init_val = 0,
	};

	teid_qfi_table = rte_hash_create(&hash_params);
	if (!teid_qfi_table) {
		perror("Failed to create hash table");
		json_decref(root);
		return;
	}

	int count = 0;
	size_t index;
	json_t *value;
	json_array_foreach(rules_array, index, value) {
		json_t *match = json_object_get(value, "match");
		json_t *action = json_object_get(value, "action");

		json_t *teid_val = json_object_get(json_object_get(match, "scalars.qos_metadata_t@teid_qfi"), "value");
		json_t *rb_val = json_object_get(json_object_get(action, "data"), "rb");
		json_t *rqi_val = json_object_get(json_object_get(action, "data"), "rqi");

		if (!json_is_string(teid_val) || !json_is_object(rb_val) || !json_is_object(rqi_val)) {
			fprintf(stderr, "Error: Missing expected TEID, RB, or RQI value in JSON\n");
			continue;
		}

		const char *teid_str = json_string_value(teid_val);
		const char *rb_str = json_string_value(json_object_get(rb_val, "value"));
		const char *rqi_str = json_string_value(json_object_get(rqi_val, "value"));

		if (!teid_str || !rb_str || !rqi_str) {
			fprintf(stderr, "Error: Null value for TEID, RB, or RQI in JSON\n");
			continue;
		}

		uint32_t teid = (uint32_t)strtol(teid_str, NULL, 10);
		uint16_t rb = (uint16_t)strtol(rb_str, NULL, 10);
		uint8_t rqi = (uint8_t)strtol(rqi_str, NULL, 10);

		struct teid_qfi_entry entry;
		entry.teid = teid;
		entry.rbnumber = rb;
		entry.rqibool = rqi;

		int ret = rte_hash_add_key_data(teid_qfi_table, &entry.teid, (void *)(uintptr_t)rb);
		if (ret < 0) {
			printf("Failed to add entry to teid_qfi_table for TEID %u\n", entry.teid);
		} else if (count < 5) {
			// Print first 5 entries added
			printf("Added to teid_qfi_table: TEID = %u, RB = %u \n", entry.teid, rb);
			count++;
		}
	}

	json_decref(root);
}

uint32_t
rte_ipv4_str_to_addr(const char *ip_str)
{
	struct in_addr ip_addr;
	if (inet_aton(ip_str, &ip_addr)) {
		return rte_be_to_cpu_32(ip_addr.s_addr);
	}
	return 0;
}

void
populate_ul_teid_table(const char *filename)
{
	json_t *root;
	json_error_t error;

	root = json_load_file(filename, 0, &error);
	if (!root) {
		fprintf(stderr, "Error: on line %d: %s\n", error.line, error.text);
		return;
	}

	json_t *tables = json_object_get(root, "tables");
	if (!json_is_object(tables)) {
		fprintf(stderr, "Error: 'tables' is not an object\n");
		json_decref(root);
		return;
	}

	json_t *ul_teid_table_json = json_object_get(tables, "ingress::UL_ASSIGN_TEID");
	if (!json_is_object(ul_teid_table_json)) {
		fprintf(stderr, "Error: 'ingress::UL_ASSIGN_TEID' is not an object\n");
		json_decref(root);
		return;
	}

	json_t *rules_array = json_object_get(ul_teid_table_json, "rules");
	if (!json_is_array(rules_array)) {
		fprintf(stderr, "Error: 'rules' is not an array\n");
		json_decref(root);
		return;
	}

	struct rte_hash_parameters hash_params = {
		.name = UL_HASH_TABLE_NAME,
		.entries = UL_HASH_TABLE_SIZE,
		.key_len = sizeof(uint32_t),
		.hash_func = rte_jhash,
		.hash_func_init_val = 0,
	};

	ul_teid_table = rte_hash_create(&hash_params);
	if (!ul_teid_table) {
		perror("Failed to create UL hash table");
		json_decref(root);
		return;
	}
	int count = 0;

	size_t index;
	json_t *value;
	json_array_foreach(rules_array, index, value) {
		json_t *match = json_object_get(value, "match");
		json_t *action = json_object_get(value, "action");

		json_t *ip_val = json_object_get(json_object_get(match, "ipv4.dstAddr"), "value");
		json_t *teid_val = json_object_get(json_object_get(action, "data"), "teid");

		if (!json_is_string(ip_val) || !json_is_object(teid_val)) {
			fprintf(stderr, "Error: Missing expected IP or TEID value in JSON\n");
			continue;
		}

		const char *ip_str = json_string_value(ip_val);
		const char *teid_str = json_string_value(json_object_get(teid_val, "value"));

		if (!ip_str || !teid_str) {
			fprintf(stderr, "Error: Null value for IP or TEID in JSON\n");
			continue;
		}

		uint32_t ip;
		inet_pton(AF_INET, ip_str, &ip);
		uint32_t teid = (uint32_t)strtol(teid_str, NULL, 10);

		struct ul_teid_entry entry = { teid };
		int ret = rte_hash_add_key_data(ul_teid_table, &ip, (void *)(uintptr_t)teid);
		if (ret < 0) {
			printf("Failed to add entry to ul_teid_table for IP %s\n", ip_str);
		} else if (count < 5) {
			// Print first 5 entries added
			printf("Added to ul_teid_table: IP = %s, TEID = %u\n", ip_str, teid);
			count++;
		}

		(void)entry;
	}

	json_decref(root);
}

/* main processing loop */
static int
main_intr_loop(__rte_unused void *dummy)
{
	struct rte_mbuf *pkts_burst[MAX_PKT_BURST];
	unsigned int lcore_id;
	uint64_t prev_tsc, diff_tsc, cur_tsc;
	int i, j, nb_rx;
	uint8_t queueid;
	uint16_t portid;
	struct lcore_conf *qconf;
	struct lcore_rx_queue *rx_queue;
	uint32_t lcore_rx_idle_count = 0;
	uint32_t lcore_idle_hint = 0;
	int intr_en = 0;

	const uint64_t drain_tsc = (rte_get_tsc_hz() + US_PER_S - 1) /
				   US_PER_S * BURST_TX_DRAIN_US;

	prev_tsc = 0;

	lcore_id = rte_lcore_id();
	qconf = &lcore_conf[lcore_id];

	if (qconf->n_rx_queue == 0) {
		RTE_LOG(INFO, L3FWD_POWER, "lcore %u has nothing to do\n",
			lcore_id);
		return 0;
	}

	RTE_LOG(INFO, L3FWD_POWER, "entering main interrupt loop on lcore %u\n",
		lcore_id);

	for (i = 0; i < qconf->n_rx_queue; i++) {
		portid = qconf->rx_queue_list[i].port_id;
		queueid = qconf->rx_queue_list[i].queue_id;
		RTE_LOG(INFO, L3FWD_POWER,
			" -- lcoreid=%u portid=%u rxqueueid=%hhu\n",
			lcore_id, portid, queueid);
	}

	/* add into event wait list */
	if (event_register(qconf) == 0)
		intr_en = 1;
	else
		RTE_LOG(INFO, L3FWD_POWER, "RX interrupt won't enable.\n");

	while (!is_done()) {
		stats[lcore_id].nb_iteration_looped++;

		cur_tsc = rte_rdtsc();

		/*
		 * TX burst queue drain
		 */
		diff_tsc = cur_tsc - prev_tsc;
		if (unlikely(diff_tsc > drain_tsc)) {
			for (i = 0; i < qconf->n_tx_port; ++i) {
				portid = qconf->tx_port_id[i];
				rte_eth_tx_buffer_flush(portid,
					qconf->tx_queue_id[portid],
					qconf->tx_buffer[portid]);
			}
			prev_tsc = cur_tsc;
		}

start_rx:
		/*
		 * Read packet from RX queues
		 */
		lcore_rx_idle_count = 0;
		for (i = 0; i < qconf->n_rx_queue; ++i) {
			rx_queue = &(qconf->rx_queue_list[i]);
			rx_queue->idle_hint = 0;
			portid = rx_queue->port_id;
			queueid = rx_queue->queue_id;

			nb_rx = rte_eth_rx_burst(portid, queueid, pkts_burst,
					MAX_PKT_BURST);

			/* Add Your Tracing Instrumentation Here: observe every RX poll result so tracing can anchor, trigger, count captured packets, and stop automatically. */
			trace_capture_handle_rx(lcore_id, portid, (uint16_t)nb_rx);

			stats[lcore_id].nb_rx_processed += nb_rx;
			if (unlikely(nb_rx == 0)) {
				/**
				 * no packet received from rx queue, try to
				 * sleep for a while forcing CPU enter deeper
				 * C states.
				 */
				rx_queue->zero_rx_packet_count++;

				if (rx_queue->zero_rx_packet_count <=
						MIN_ZERO_POLL_COUNT)
					continue;

				/* Power Mng Algortimgn heriotic: translate repeated empty polls into an idle hint that decides how aggressively the core should back off. */
				rx_queue->idle_hint = power_idle_heuristic(
						rx_queue->zero_rx_packet_count);
				lcore_rx_idle_count++;
			} else {
				rx_queue->zero_rx_packet_count = 0;
			}

			/* Prefetch first packets */
			for (j = 0; j < PREFETCH_OFFSET && j < nb_rx; j++) {
				rte_prefetch0(rte_pktmbuf_mtod(
						pkts_burst[j], void *));
			}

			/* Prefetch and forward already prefetched packets */
			for (j = 0; j < (nb_rx - PREFETCH_OFFSET); j++) {
				rte_prefetch0(rte_pktmbuf_mtod(
						pkts_burst[j + PREFETCH_OFFSET],
						void *));

				handle_packet(pkts_burst[j], portid);
				l2fwd_simple_forward(pkts_burst[j], portid);
			}

			/* Forward remaining prefetched packets */
			for (; j < nb_rx; j++) {
				handle_packet(pkts_burst[j], portid);
				l2fwd_simple_forward(pkts_burst[j], portid);
			}
		}

		if (unlikely(lcore_rx_idle_count == qconf->n_rx_queue)) {
			/**
			 * All Rx queues empty in recent consecutive polls,
			 * sleep in a conservative manner, meaning sleep as
			 * less as possible.
			 */
			for (i = 1,
			    lcore_idle_hint = qconf->rx_queue_list[0].idle_hint;
					i < qconf->n_rx_queue; ++i) {
				rx_queue = &(qconf->rx_queue_list[i]);
				if (rx_queue->idle_hint < lcore_idle_hint)
					lcore_idle_hint = rx_queue->idle_hint;
			}

			if (lcore_idle_hint < SUSPEND_THRESHOLD) {
				/**
				 * execute "pause" instruction to avoid context
				 * switch which generally take hundred of
				 * microseconds for short sleep.
				 */
				/* Power Mng Algortimgn heriotic: short idle hints stay in polling context and use a brief delay instead of full interrupt sleep. */
				rte_delay_us(lcore_idle_hint);
				RTE_LOG(INFO, L3FWD_POWER,
					"lcore_idle_hint %u is going to pause\n",
					lcore_idle_hint);

			} else {
				/* suspend until rx interrupt triggers */
				if (intr_en) {
					turn_on_off_intr(qconf, 1);
					RTE_LOG(INFO, L3FWD_POWER,
						"lcore_idle_hint %u is going to sleep\n",
						lcore_idle_hint);

					/* Power Mng Algortimgn heriotic: long idle hints switch to interrupt-driven sleep so the core can enter deeper idle states and wake on RX activity. */
					sleep_until_rx_interrupt(
						qconf->n_rx_queue);
					turn_on_off_intr(qconf, 0);
					/**
					 * start receiving packets immediately
					 */
					if (likely(!is_done()))
						goto start_rx;
				}
			}
			stats[lcore_id].sleep_time += lcore_idle_hint;
		}
	}

	return 0;
}

/* main processing loop */
static int
main_telemetry_loop(__rte_unused void *dummy)
{
	struct rte_mbuf *pkts_burst[MAX_PKT_BURST];
	unsigned int lcore_id;
	uint64_t prev_tsc, diff_tsc, cur_tsc, prev_tel_tsc;
	int i, j, nb_rx;
	uint8_t queueid;
	uint16_t portid;
	struct lcore_conf *qconf;
	struct lcore_rx_queue *rx_queue;
	uint64_t ep_nep[2] = {0}, fp_nfp[2] = {0};
	uint64_t poll_count;
	enum busy_rate br;

	const uint64_t drain_tsc = (rte_get_tsc_hz() + US_PER_S - 1) /
					US_PER_S * BURST_TX_DRAIN_US;

	poll_count = 0;
	prev_tsc = 0;
	prev_tel_tsc = 0;

	lcore_id = rte_lcore_id();
	qconf = &lcore_conf[lcore_id];

	if (qconf->n_rx_queue == 0) {
		RTE_LOG(INFO, L3FWD_POWER, "lcore %u has nothing to do\n",
			lcore_id);
		return 0;
	}

	RTE_LOG(INFO, L3FWD_POWER, "entering main telemetry loop on lcore %u\n",
		lcore_id);

	for (i = 0; i < qconf->n_rx_queue; i++) {
		portid = qconf->rx_queue_list[i].port_id;
		queueid = qconf->rx_queue_list[i].queue_id;
		RTE_LOG(INFO, L3FWD_POWER, " -- lcoreid=%u portid=%u "
			"rxqueueid=%hhu\n", lcore_id, portid, queueid);
	}

	while (!is_done()) {
		cur_tsc = rte_rdtsc();
		/*
		 * TX burst queue drain
		 */
		diff_tsc = cur_tsc - prev_tsc;
		if (unlikely(diff_tsc > drain_tsc)) {
			for (i = 0; i < qconf->n_tx_port; ++i) {
				portid = qconf->tx_port_id[i];
				rte_eth_tx_buffer_flush(portid,
					qconf->tx_queue_id[portid],
					qconf->tx_buffer[portid]);
			}
			prev_tsc = cur_tsc;
		}

		/*
		 * Read packet from RX queues
		 */
		for (i = 0; i < qconf->n_rx_queue; ++i) {
			rx_queue = &(qconf->rx_queue_list[i]);
			portid = rx_queue->port_id;
			queueid = rx_queue->queue_id;

			nb_rx = rte_eth_rx_burst(portid, queueid, pkts_burst,
					MAX_PKT_BURST);

			/* Add Your Tracing Instrumentation Here: tap every RX burst outcome even in telemetry mode so the same runtime trace gating logic can be reused across modes. */
			trace_capture_handle_rx(lcore_id, portid, (uint16_t)nb_rx);

			ep_nep[nb_rx == 0]++;
			fp_nfp[nb_rx == MAX_PKT_BURST]++;
			poll_count++;
			if (unlikely(nb_rx == 0))
				continue;

			/* Prefetch first packets */
			for (j = 0; j < PREFETCH_OFFSET && j < nb_rx; j++) {
				rte_prefetch0(rte_pktmbuf_mtod(
						pkts_burst[j], void *));
			}

			/* Prefetch and forward already prefetched packets */
			for (j = 0; j < (nb_rx - PREFETCH_OFFSET); j++) {
				rte_prefetch0(rte_pktmbuf_mtod(pkts_burst[
						j + PREFETCH_OFFSET], void *));
				l3fwd_simple_forward(pkts_burst[j], portid,
					qconf);
			}

			/* Forward remaining prefetched packets */
			for (; j < nb_rx; j++) {
				l3fwd_simple_forward(pkts_burst[j], portid,
					qconf);
			}
		}
		if (unlikely(poll_count >= DEFAULT_COUNT)) {
			diff_tsc = cur_tsc - prev_tel_tsc;
			if (diff_tsc >= MAX_CYCLES) {
				br = FULL;
			} else if (diff_tsc > MIN_CYCLES &&
					diff_tsc < MAX_CYCLES) {
				br = (diff_tsc * 100) / MAX_CYCLES;
			} else {
				br = ZERO;
			}
			poll_count = 0;
			prev_tel_tsc = cur_tsc;
			/* update stats for telemetry */
			rte_spinlock_lock(&stats[lcore_id].telemetry_lock);
			stats[lcore_id].ep_nep[0] = ep_nep[0];
			stats[lcore_id].ep_nep[1] = ep_nep[1];
			stats[lcore_id].fp_nfp[0] = fp_nfp[0];
			stats[lcore_id].fp_nfp[1] = fp_nfp[1];
			stats[lcore_id].br = br;
			rte_spinlock_unlock(&stats[lcore_id].telemetry_lock);
		}
	}

	return 0;
}

/* main processing loop */
static int
main_empty_poll_loop(__rte_unused void *dummy)
{
	struct rte_mbuf *pkts_burst[MAX_PKT_BURST];
	unsigned int lcore_id;
	uint64_t prev_tsc, diff_tsc, cur_tsc;
	int i, j, nb_rx;
	uint8_t queueid;
	uint16_t portid;
	struct lcore_conf *qconf;
	struct lcore_rx_queue *rx_queue;

	const uint64_t drain_tsc =
		(rte_get_tsc_hz() + US_PER_S - 1) /
		US_PER_S * BURST_TX_DRAIN_US;

	prev_tsc = 0;

	lcore_id = rte_lcore_id();
	qconf = &lcore_conf[lcore_id];

	if (qconf->n_rx_queue == 0) {
		RTE_LOG(INFO, L3FWD_POWER, "lcore %u has nothing to do\n",
			lcore_id);
		return 0;
	}

	for (i = 0; i < qconf->n_rx_queue; i++) {
		portid = qconf->rx_queue_list[i].port_id;
		queueid = qconf->rx_queue_list[i].queue_id;
		RTE_LOG(INFO, L3FWD_POWER, " -- lcoreid=%u portid=%u "
			"rxqueueid=%hhu\n", lcore_id, portid, queueid);
	}

	while (!is_done()) {
		stats[lcore_id].nb_iteration_looped++;

		cur_tsc = rte_rdtsc();
		/*
		 * TX burst queue drain
		 */
		diff_tsc = cur_tsc - prev_tsc;
		if (unlikely(diff_tsc > drain_tsc)) {
			for (i = 0; i < qconf->n_tx_port; ++i) {
				portid = qconf->tx_port_id[i];
				rte_eth_tx_buffer_flush(portid,
						qconf->tx_queue_id[portid],
						qconf->tx_buffer[portid]);
			}
			prev_tsc = cur_tsc;
		}

		/*
		 * Read packet from RX queues
		 */
		for (i = 0; i < qconf->n_rx_queue; ++i) {
			rx_queue = &(qconf->rx_queue_list[i]);
			rx_queue->idle_hint = 0;
			portid = rx_queue->port_id;
			queueid = rx_queue->queue_id;

			nb_rx = rte_eth_rx_burst(portid, queueid, pkts_burst,
					MAX_PKT_BURST);

			/* Add Your Tracing Instrumentation Here: apply the same trace hook in empty-poll mode so idle-heavy experiments can still generate aligned trace data. */
			trace_capture_handle_rx(lcore_id, portid, (uint16_t)nb_rx);

			stats[lcore_id].nb_rx_processed += nb_rx;

			if (nb_rx == 0) {
				rte_power_empty_poll_stat_update(lcore_id);
				continue;
			} else {
				rte_power_poll_stat_update(lcore_id, nb_rx);
			}

			/* Prefetch first packets */
			for (j = 0; j < PREFETCH_OFFSET && j < nb_rx; j++) {
				rte_prefetch0(rte_pktmbuf_mtod(
						pkts_burst[j], void *));
			}

			/* Prefetch and forward already prefetched packets */
			for (j = 0; j < (nb_rx - PREFETCH_OFFSET); j++) {
				rte_prefetch0(rte_pktmbuf_mtod(pkts_burst[
						j + PREFETCH_OFFSET], void *));
				l3fwd_simple_forward(pkts_burst[j], portid,
					qconf);
			}

			/* Forward remaining prefetched packets */
			for (; j < nb_rx; j++) {
				l3fwd_simple_forward(pkts_burst[j], portid,
					qconf);
			}
		}
	}

	return 0;
}

//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*---*-*-*-*-*-*-*-*-*-*-*
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
//*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*
//-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*-*//
/* Add Your Tracing Instrumentation Here: keep per-lcore counters and snapshots for empty polls, non-empty polls, and packet rates so users can add lightweight poll-side analysis in legacy mode. */
/*—— per-lcore counters and snapshots ——*/
static uint64_t empty_cnt[RTE_MAX_LCORE];
static uint64_t nonempty_cnt[RTE_MAX_LCORE];
static uint64_t pkt_cnt[RTE_MAX_LCORE];

static uint64_t last_empty[RTE_MAX_LCORE];
static uint64_t last_nonempty[RTE_MAX_LCORE];
static uint64_t last_pkts[RTE_MAX_LCORE];

static uint64_t last_empty_tsc[RTE_MAX_LCORE];
static uint64_t last_nonempty_tsc[RTE_MAX_LCORE];
static uint64_t last_pkts_tsc[RTE_MAX_LCORE];

/* Add Your Tracing Instrumentation Here: classify each RX poll as empty or non-empty and accumulate packet counts so custom trace-adjacent statistics can be exported later. */
/*—— record each burst’s outcome ——*/
static inline void
record_rx_poll(unsigned lcore_id, uint16_t nb_rx)
{
	if (nb_rx == 0)
		empty_cnt[lcore_id]++;
	else
		nonempty_cnt[lcore_id]++;

	pkt_cnt[lcore_id] += nb_rx;
}

/* Add Your Tracing Instrumentation Here: compute per-second rates from the poll counters using TSC deltas so users can tailor their own analysis outputs. */
/*—— helper to compute “per-second” rate ——*/
static inline uint64_t
calc_rate(uint64_t *cnt,
	  uint64_t *last_cnt,
	  uint64_t *last_tsc,
	  unsigned lcore_id)
{
	uint64_t now = rte_rdtsc();
	if (last_tsc[lcore_id] == 0) {
		/* first call for this core: initialize */
		last_tsc[lcore_id] = now;
		last_cnt[lcore_id] = cnt[lcore_id];
		return 0;
	}
	uint64_t dc = cnt[lcore_id] - last_cnt[lcore_id];
	uint64_t dt = now - last_tsc[lcore_id];
	uint64_t hz = rte_get_tsc_hz();
	uint64_t rate = dt ? (dc * hz) / dt : 0;

	last_cnt[lcore_id] = cnt[lcore_id];
	last_tsc[lcore_id] = now;
	return rate;
}

/* Add Your Tracing Instrumentation Here: helper getters expose empty-poll rate, non-empty-poll rate, and RX pps for anyone extending the legacy loop. */
/*—— getters for empty / non-empty / pps rates ——*/
static inline uint64_t
get_empty_polls_per_sec(unsigned lcore_id)
{
	return calc_rate(empty_cnt,
			 last_empty,
			 last_empty_tsc,
			 lcore_id);
}

static inline uint64_t
get_nonempty_polls_per_sec(unsigned lcore_id)
{
	return calc_rate(nonempty_cnt,
			 last_nonempty,
			 last_nonempty_tsc,
			 lcore_id);
}

static inline uint64_t
get_rx_pps(unsigned lcore_id)
{
	return calc_rate(pkt_cnt,
			 last_pkts,
			 last_pkts_tsc,
			 lcore_id);
}

static int
main_legacy_loop(__rte_unused void *dummy)
{
	struct rte_mbuf *pkts_burst[MAX_PKT_BURST];
	unsigned lcore_id;
	uint64_t prev_tsc, diff_tsc, cur_tsc, tim_res_tsc, hz;
	uint64_t prev_tsc_power = 0, cur_tsc_power, diff_tsc_power;
	int i, j, nb_rx;
	uint8_t queueid;
	uint16_t portid;
	struct lcore_conf *qconf;
	struct lcore_rx_queue *rx_queue;
	enum freq_scale_hint_t lcore_scaleup_hint;
	uint32_t lcore_rx_idle_count = 0;
	uint32_t lcore_idle_hint = 0;
	int intr_en = 0;
	int ret;
	const uint64_t drain_tsc = (rte_get_tsc_hz() + US_PER_S - 1) / US_PER_S * BURST_TX_DRAIN_US;

	prev_tsc = 0;
	hz = rte_get_timer_hz();
	tim_res_tsc = hz / TIMER_NUMBER_PER_SECOND;

	lcore_id = rte_lcore_id();
	qconf = &lcore_conf[lcore_id];

	static uint64_t start_zero_tsc[RTE_MAX_LCORE][1];
	(void)start_zero_tsc;
	(void)ret;

	if (qconf->n_rx_queue == 0) {
		RTE_LOG(INFO, L3FWD_POWER, "lcore %u has nothing to do\n", lcore_id);
		return 0;
	}

	RTE_LOG(INFO, L3FWD_POWER, "entering main loop on lcore %u\n", lcore_id);

	for (i = 0; i < qconf->n_rx_queue; i++) {
		portid = qconf->rx_queue_list[i].port_id;
		queueid = qconf->rx_queue_list[i].queue_id;
		RTE_LOG(INFO, L3FWD_POWER, " -- lcoreid=%u portid=%u "
			"rxqueueid=%hhu\n", lcore_id, portid, queueid);
	}

	/* add into event wait list */
	if (event_register(qconf) == 0)
		intr_en = 1;
	else
		RTE_LOG(INFO, L3FWD_POWER, "RX interrupt won't enable.\n");

	while (!is_done()) {
		stats[lcore_id].nb_iteration_looped++;

		cur_tsc = rte_rdtsc();
		cur_tsc_power = cur_tsc;

		/*
		 * TX burst queue drain
		 */
		diff_tsc = cur_tsc - prev_tsc;
		if (unlikely(diff_tsc > drain_tsc)) {
			for (i = 0; i < qconf->n_tx_port; ++i) {
				portid = qconf->tx_port_id[i];
				rte_eth_tx_buffer_flush(portid,
						qconf->tx_queue_id[portid],
						qconf->tx_buffer[portid]);
			}
			prev_tsc = cur_tsc;
		}

		diff_tsc_power = cur_tsc_power - prev_tsc_power;
		if (diff_tsc_power > tim_res_tsc) {
			/* Power Mng Algortimgn heriotic: periodically service the per-lcore power timer so background down-scaling decisions can run while the legacy loop is active. */
			rte_timer_manage();
			prev_tsc_power = cur_tsc_power;
		}

start_rx:
		/*
		 * Read packet from RX queues
		 */
		lcore_scaleup_hint = FREQ_CURRENT;
		lcore_rx_idle_count = 0;
		for (i = 0; i < qconf->n_rx_queue; ++i) {
			rx_queue = &(qconf->rx_queue_list[i]);
			rx_queue->idle_hint = 0;
			portid = rx_queue->port_id;
			queueid = rx_queue->queue_id;

			nb_rx = rte_eth_rx_burst(portid, queueid, pkts_burst,
						MAX_PKT_BURST);

			/* Add Your Tracing Instrumentation Here: legacy poll-site hook for anchor generation, runtime trace trigger, bounded packet counting, and automatic trace stop. */
			trace_capture_handle_rx(lcore_id, portid, (uint16_t)nb_rx);

			/* Add Your Tracing Instrumentation Here: per-poll local counters let users extend the legacy loop with lightweight custom RX-rate analysis. */
			record_rx_poll(lcore_id, (uint16_t)nb_rx);

			stats[lcore_id].nb_rx_processed += nb_rx;
			if (unlikely(nb_rx <= 0)) {
				/**
				 * no packet received from rx queue, try to
				 * sleep for a while forcing CPU enter deeper
				 * C states.
				 */
				rx_queue->zero_rx_packet_count++;

				if (rx_queue->zero_rx_packet_count <=
						MIN_ZERO_POLL_COUNT)
					continue;

				/* Power Mng Algortimgn heriotic: use the zero-poll run length to derive an idle hint for later sleep/backoff handling. */
				rx_queue->idle_hint = power_idle_heuristic(
					rx_queue->zero_rx_packet_count);

				lcore_rx_idle_count++;
			} else {
				rx_queue->zero_rx_packet_count = 0;

				/**
				 * do not scale up frequency immediately as
				 * user to kernel space communication is costly
				 * which might impact packet I/O for received
				 * packets.
				 */
				/* Power Mng Algortimgn heriotic: estimate whether this queue needs a frequency increase based on recent RX queue occupancy and trend. */
				rx_queue->freq_up_hint =
					power_freq_scaleup_heuristic(lcore_id,
							portid, queueid);
			}

			/* Prefetch first packets */
			for (j = 0; j < PREFETCH_OFFSET && j < nb_rx; j++) {
				rte_prefetch0(rte_pktmbuf_mtod(
					pkts_burst[j], void *));
			}

			/* Prefetch and forward already prefetched packets */
			for (j = 0; j < (nb_rx - PREFETCH_OFFSET); j++) {
				rte_prefetch0(rte_pktmbuf_mtod(pkts_burst[
					j + PREFETCH_OFFSET], void *));
				handle_packet(pkts_burst[j], portid);
				l2fwd_simple_forward(pkts_burst[j], portid);
			}

			/* Forward remaining prefetched packets */
			for (; j < nb_rx; j++) {
				handle_packet(pkts_burst[j], portid);
				l2fwd_simple_forward(pkts_burst[j], portid);
			}
		}

		if (likely(lcore_rx_idle_count != qconf->n_rx_queue)) {
			for (i = 1, lcore_scaleup_hint =
				qconf->rx_queue_list[0].freq_up_hint;
					i < qconf->n_rx_queue; ++i) {
				rx_queue = &(qconf->rx_queue_list[i]);
				if (rx_queue->freq_up_hint >
						lcore_scaleup_hint)
					lcore_scaleup_hint =
						rx_queue->freq_up_hint;
			}

			if (lcore_scaleup_hint == FREQ_HIGHEST) {
				if (rte_power_freq_max)
					/* Power Mng Algortimgn heriotic: jump directly to the highest available frequency when queue pressure is strong enough. */
					rte_power_freq_max(lcore_id);
			} else if (lcore_scaleup_hint == FREQ_HIGHER) {
				if (rte_power_freq_up)
					/* Power Mng Algortimgn heriotic: step the frequency upward gradually when the trend suggests more responsiveness is needed. */
					rte_power_freq_up(lcore_id);
			}
		} else {
			/**
			 * All Rx queues empty in recent consecutive polls,
			 * sleep in a conservative manner, meaning sleep as
			 * less as possible.
			 */
			for (i = 1, lcore_idle_hint =
				qconf->rx_queue_list[0].idle_hint;
					i < qconf->n_rx_queue; ++i) {

				rx_queue = &(qconf->rx_queue_list[i]);
				if (rx_queue->idle_hint < lcore_idle_hint)
					lcore_idle_hint = rx_queue->idle_hint;
			}

			// if (lcore_idle_hint < SUSPEND_THRESHOLD) {
			// 	rte_delay_us(lcore_idle_hint);
			// } else {
			// 	/* suspend until rx interrupt triggers */
			// 	if (intr_en) {
			// 		turn_on_off_intr(qconf, 1);

			// 		sleep_until_rx_interrupt(
			// 			qconf->n_rx_queue);
			// 		turn_on_off_intr(qconf, 0);
			// 		/**
			// 		 * start receiving packets immediately
			// 		 */
			// 		if (likely(!is_done()))
			// 			goto start_rx;
			// 	}
			// }
			/* Power Mng Algortimgn heriotic: accumulate the selected idle hint so the timer callback can later decide whether frequency should be reduced. */
			stats[lcore_id].sleep_time += lcore_idle_hint;
		}
	}
	return 0;
}

static int
check_lcore_params(void)
{
	uint8_t queue, lcore;
	uint16_t i;
	int socketid;

	for (i = 0; i < nb_lcore_params; ++i) {
		queue = lcore_params[i].queue_id;
		if (queue >= MAX_RX_QUEUE_PER_PORT) {
			printf("invalid queue number: %hhu\n", queue);
			return -1;
		}
		lcore = lcore_params[i].lcore_id;
		if (!rte_lcore_is_enabled(lcore)) {
			printf("error: lcore %hhu is not enabled in lcore "
				"mask\n", lcore);
			return -1;
		}
		socketid = rte_lcore_to_socket_id(lcore);
		if ((socketid != 0) && (numa_on == 0)) {
			printf("warning: lcore %hhu is on socket %d with numa "
				"off\n", lcore, socketid);
		}
		if (app_mode == APP_MODE_TELEMETRY && lcore == rte_lcore_id()) {
			printf("cannot enable master core %d in config for telemetry mode\n",
				rte_lcore_id());
			return -1;
		}
	}
	return 0;
}

static int
check_port_config(void)
{
	unsigned portid;
	uint16_t i;

	for (i = 0; i < nb_lcore_params; ++i) {
		portid = lcore_params[i].port_id;
		if ((enabled_port_mask & (1 << portid)) == 0) {
			printf("port %u is not enabled in port mask\n",
				portid);
			return -1;
		}
		if (!rte_eth_dev_is_valid_port(portid)) {
			printf("port %u is not present on the board\n",
				portid);
			return -1;
		}
	}
	return 0;
}

static uint8_t
get_port_n_rx_queues(const uint16_t port)
{
	int queue = -1;
	uint16_t i;

	for (i = 0; i < nb_lcore_params; ++i) {
		if (lcore_params[i].port_id == port &&
				lcore_params[i].queue_id > queue)
			queue = lcore_params[i].queue_id;
	}
	return (uint8_t)(++queue);
}

static int
init_lcore_rx_queues(void)
{
	uint16_t i, nb_rx_queue;
	uint8_t lcore;

	for (i = 0; i < nb_lcore_params; ++i) {
		lcore = lcore_params[i].lcore_id;
		nb_rx_queue = lcore_conf[lcore].n_rx_queue;
		if (nb_rx_queue >= MAX_RX_QUEUE_PER_LCORE) {
			printf("error: too many queues (%u) for lcore: %u\n",
				(unsigned)nb_rx_queue + 1, (unsigned)lcore);
			return -1;
		} else {
			lcore_conf[lcore].rx_queue_list[nb_rx_queue].port_id =
				lcore_params[i].port_id;
			lcore_conf[lcore].rx_queue_list[nb_rx_queue].queue_id =
				lcore_params[i].queue_id;
			lcore_conf[lcore].n_rx_queue++;
		}
	}
	return 0;
}

/* display usage */
static void
print_usage(const char *prgname)
{
	printf("%s [EAL options] -- -p PORTMASK -P"
		"  [--config (port,queue,lcore)[,(port,queue,lcore]]"
		"  [--high-perf-cores CORELIST"
		"  [--perf-config (port,queue,hi_perf,lcore_index)[,(port,queue,hi_perf,lcore_index]]"
		"  [--enable-jumbo [--max-pkt-len PKTLEN]]\n"
		"  -p PORTMASK: hexadecimal bitmask of ports to configure\n"
		"  -P : enable promiscuous mode\n"
		"  --config (port,queue,lcore): rx queues configuration\n"
		"  --high-perf-cores CORELIST: list of high performance cores\n"
		"  --perf-config: similar as config, cores specified as indices"
		" for bins containing high or regular performance cores\n"
		"  --no-numa: optional, disable numa awareness\n"
		"  --enable-jumbo: enable jumbo frame"
		" which max packet len is PKTLEN in decimal (64-9600)\n"
		"  --parse-ptype: parse packet type by software\n"
		"  --legacy: use legacy interrupt-based scaling\n"
		"  --empty-poll: enable empty poll detection"
		" follow (training_flag, high_threshold, med_threshold)\n"
		" --telemetry: enable telemetry mode, to update"
		" empty polls, full polls, and core busyness to telemetry\n"
		" --interrupt-only: enable interrupt-only mode\n",
		prgname);
}

static int
parse_max_pkt_len(const char *pktlen)
{
	char *end = NULL;
	unsigned long len;

	/* parse decimal string */
	len = strtoul(pktlen, &end, 10);
	if ((pktlen[0] == '\0') || (end == NULL) || (*end != '\0'))
		return -1;

	if (len == 0)
		return -1;

	return len;
}

static int
parse_portmask(const char *portmask)
{
	char *end = NULL;
	unsigned long pm;

	/* parse hexadecimal string */
	pm = strtoul(portmask, &end, 16);
	if ((portmask[0] == '\0') || (end == NULL) || (*end != '\0'))
		return 0;

	return pm;
}

static int
parse_config(const char *q_arg)
{
	char s[256];
	const char *p, *p0 = q_arg;
	char *end;
	enum fieldnames {
		FLD_PORT = 0,
		FLD_QUEUE,
		FLD_LCORE,
		_NUM_FLD
	};
	unsigned long int_fld[_NUM_FLD];
	char *str_fld[_NUM_FLD];
	int i;
	unsigned size;

	nb_lcore_params = 0;

	while ((p = strchr(p0, '(')) != NULL) {
		++p;
		if ((p0 = strchr(p, ')')) == NULL)
			return -1;

		size = p0 - p;
		if (size >= sizeof(s))
			return -1;

		snprintf(s, sizeof(s), "%.*s", size, p);
		if (rte_strsplit(s, sizeof(s), str_fld, _NUM_FLD, ',') !=
				_NUM_FLD)
			return -1;
		for (i = 0; i < _NUM_FLD; i++) {
			errno = 0;
			int_fld[i] = strtoul(str_fld[i], &end, 0);
			if (errno != 0 || end == str_fld[i] || int_fld[i] > 255)
				return -1;
		}
		if (nb_lcore_params >= MAX_LCORE_PARAMS) {
			printf("exceeded max number of lcore params: %hu\n",
				nb_lcore_params);
			return -1;
		}
		lcore_params_array[nb_lcore_params].port_id =
				(uint8_t)int_fld[FLD_PORT];
		lcore_params_array[nb_lcore_params].queue_id =
				(uint8_t)int_fld[FLD_QUEUE];
		lcore_params_array[nb_lcore_params].lcore_id =
				(uint8_t)int_fld[FLD_LCORE];
		++nb_lcore_params;
	}
	lcore_params = lcore_params_array;

	return 0;
}

static int
parse_ep_config(const char *q_arg)
{
	char s[256];
	const char *p = q_arg;
	char *end;
	int num_arg;

	char *str_fld[3];

	int training_flag;
	int med_edpi;
	int hgh_edpi;

	ep_med_edpi = EMPTY_POLL_MED_THRESHOLD;
	ep_hgh_edpi = EMPTY_POLL_MED_THRESHOLD;

	strlcpy(s, p, sizeof(s));

	num_arg = rte_strsplit(s, sizeof(s), str_fld, 3, ',');

	empty_poll_train = false;

	if (num_arg == 0)
		return 0;

	if (num_arg == 3) {
		training_flag = strtoul(str_fld[0], &end, 0);
		med_edpi = strtoul(str_fld[1], &end, 0);
		hgh_edpi = strtoul(str_fld[2], &end, 0);

		if (training_flag == 1)
			empty_poll_train = true;

		if (med_edpi > 0)
			ep_med_edpi = med_edpi;

		if (med_edpi > 0)
			ep_hgh_edpi = hgh_edpi;
	} else {
		return -1;
	}

	return 0;
}

#define CMD_LINE_OPT_PARSE_PTYPE "parse-ptype"
#define CMD_LINE_OPT_LEGACY "legacy"
#define CMD_LINE_OPT_EMPTY_POLL "empty-poll"
#define CMD_LINE_OPT_INTERRUPT_ONLY "interrupt-only"
#define CMD_LINE_OPT_TELEMETRY "telemetry"

/* Parse the argument given in the command line of the application */
static int
parse_args(int argc, char **argv)
{
	int opt, ret;
	char **argvopt;
	int option_index;
	uint32_t limit;
	char *prgname = argv[0];
	static struct option lgopts[] = {
		{"config", 1, 0, 0},
		{"perf-config", 1, 0, 0},
		{"high-perf-cores", 1, 0, 0},
		{"no-numa", 0, 0, 0},
		{"enable-jumbo", 0, 0, 0},
		{CMD_LINE_OPT_EMPTY_POLL, 1, 0, 0},
		{CMD_LINE_OPT_PARSE_PTYPE, 0, 0, 0},
		{CMD_LINE_OPT_LEGACY, 0, 0, 0},
		{CMD_LINE_OPT_TELEMETRY, 0, 0, 0},
		{CMD_LINE_OPT_INTERRUPT_ONLY, 0, 0, 0},
		{NULL, 0, 0, 0}
	};

	argvopt = argv;

	while ((opt = getopt_long(argc, argvopt, "p:l:m:h:P",
				lgopts, &option_index)) != EOF) {

		switch (opt) {
		/* portmask */
		case 'p':
			enabled_port_mask = parse_portmask(optarg);
			if (enabled_port_mask == 0) {
				printf("invalid portmask\n");
				print_usage(prgname);
				return -1;
			}
			break;
		case 'P':
			printf("Promiscuous mode selected\n");
			promiscuous_on = 1;
			break;
		case 'l':
			limit = parse_max_pkt_len(optarg);
			freq_tlb[LOW] = limit;
			break;
		case 'm':
			limit = parse_max_pkt_len(optarg);
			freq_tlb[MED] = limit;
			break;
		case 'h':
			limit = parse_max_pkt_len(optarg);
			freq_tlb[HGH] = limit;
			break;
		/* long options */
		case 0:
			if (!strncmp(lgopts[option_index].name, "config", 6)) {
				ret = parse_config(optarg);
				if (ret) {
					printf("invalid config\n");
					print_usage(prgname);
					return -1;
				}
			}

			if (!strncmp(lgopts[option_index].name,
					"perf-config", 11)) {
				ret = parse_perf_config(optarg);
				if (ret) {
					printf("invalid perf-config\n");
					print_usage(prgname);
					return -1;
				}
			}

			if (!strncmp(lgopts[option_index].name,
					"high-perf-cores", 15)) {
				ret = parse_perf_core_list(optarg);
				if (ret) {
					printf("invalid high-perf-cores\n");
					print_usage(prgname);
					return -1;
				}
			}

			if (!strncmp(lgopts[option_index].name,
					"no-numa", 7)) {
				printf("numa is disabled \n");
				numa_on = 0;
			}

			if (!strncmp(lgopts[option_index].name,
					CMD_LINE_OPT_LEGACY,
					sizeof(CMD_LINE_OPT_LEGACY))) {
				if (app_mode != APP_MODE_DEFAULT) {
					printf(" legacy mode is mutually exclusive with other modes\n");
					return -1;
				}
				app_mode = APP_MODE_LEGACY;
				printf("legacy mode is enabled\n");
			}

			if (!strncmp(lgopts[option_index].name,
					CMD_LINE_OPT_EMPTY_POLL, 10)) {
				if (app_mode != APP_MODE_DEFAULT) {
					printf(" empty-poll mode is mutually exclusive with other modes\n");
					return -1;
				}
				app_mode = APP_MODE_EMPTY_POLL;
				ret = parse_ep_config(optarg);

				if (ret) {
					printf("invalid empty poll config\n");
					print_usage(prgname);
					return -1;
				}
				printf("empty-poll is enabled\n");
			}

			if (!strncmp(lgopts[option_index].name,
					CMD_LINE_OPT_TELEMETRY,
					sizeof(CMD_LINE_OPT_TELEMETRY))) {
				if (app_mode != APP_MODE_DEFAULT) {
					printf(" telemetry mode is mutually exclusive with other modes\n");
					return -1;
				}
				app_mode = APP_MODE_TELEMETRY;
				printf("telemetry mode is enabled\n");
			}

			if (!strncmp(lgopts[option_index].name,
					CMD_LINE_OPT_INTERRUPT_ONLY,
					sizeof(CMD_LINE_OPT_INTERRUPT_ONLY))) {
				if (app_mode != APP_MODE_DEFAULT) {
					printf(" interrupt-only mode is mutually exclusive with other modes\n");
					return -1;
				}
				app_mode = APP_MODE_INTERRUPT;
				printf("interrupt-only mode is enabled\n");
			}

			if (!strncmp(lgopts[option_index].name,
					"enable-jumbo", 12)) {
				struct option lenopts =
					{"max-pkt-len", required_argument, 0, 0};

				printf("jumbo frame is enabled \n");
				port_conf.rxmode.offloads |=
						DEV_RX_OFFLOAD_JUMBO_FRAME;
				port_conf.txmode.offloads |=
						DEV_TX_OFFLOAD_MULTI_SEGS;

				/**
				 * if no max-pkt-len set, use the default value
				 * RTE_ETHER_MAX_LEN
				 */
				if (0 == getopt_long(argc, argvopt, "",
						&lenopts, &option_index)) {
					ret = parse_max_pkt_len(optarg);
					if ((ret < 64) ||
						(ret > MAX_JUMBO_PKT_LEN)) {
						printf("invalid packet "
							"length\n");
						print_usage(prgname);
						return -1;
					}
					port_conf.rxmode.max_rx_pkt_len = ret;
				}
				printf("set jumbo frame "
					"max packet length to %u\n",
					(unsigned int)port_conf.rxmode.max_rx_pkt_len);
			}

			if (!strncmp(lgopts[option_index].name,
				     CMD_LINE_OPT_PARSE_PTYPE,
				     sizeof(CMD_LINE_OPT_PARSE_PTYPE))) {
				printf("soft parse-ptype is enabled\n");
				parse_ptype = 1;
			}

			break;

		default:
			print_usage(prgname);
			return -1;
		}
	}

	if (optind >= 0)
		argv[optind - 1] = prgname;

	ret = optind - 1;
	optind = 1; /* reset getopt lib */
	return ret;
}

static void
print_ethaddr(const char *name, const struct rte_ether_addr *eth_addr)
{
	char buf[RTE_ETHER_ADDR_FMT_SIZE];
	rte_ether_format_addr(buf, RTE_ETHER_ADDR_FMT_SIZE, eth_addr);
	printf("%s%s", name, buf);
}

#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
static void
setup_hash(int socketid)
{
	struct rte_hash_parameters ipv4_l3fwd_hash_params = {
		.name = NULL,
		.entries = L3FWD_HASH_ENTRIES,
		.key_len = sizeof(struct ipv4_5tuple),
		.hash_func = DEFAULT_HASH_FUNC,
		.hash_func_init_val = 0,
	};

	struct rte_hash_parameters ipv6_l3fwd_hash_params = {
		.name = NULL,
		.entries = L3FWD_HASH_ENTRIES,
		.key_len = sizeof(struct ipv6_5tuple),
		.hash_func = DEFAULT_HASH_FUNC,
		.hash_func_init_val = 0,
	};

	unsigned i;
	int ret;
	char s[64];

	/* create ipv4 hash */
	snprintf(s, sizeof(s), "ipv4_l3fwd_hash_%d", socketid);
	ipv4_l3fwd_hash_params.name = s;
	ipv4_l3fwd_hash_params.socket_id = socketid;
	ipv4_l3fwd_lookup_struct[socketid] =
		rte_hash_create(&ipv4_l3fwd_hash_params);
	if (ipv4_l3fwd_lookup_struct[socketid] == NULL)
		rte_exit(EXIT_FAILURE, "Unable to create the l3fwd hash on "
				"socket %d\n", socketid);

	/* create ipv6 hash */
	snprintf(s, sizeof(s), "ipv6_l3fwd_hash_%d", socketid);
	ipv6_l3fwd_hash_params.name = s;
	ipv6_l3fwd_hash_params.socket_id = socketid;
	ipv6_l3fwd_lookup_struct[socketid] =
		rte_hash_create(&ipv6_l3fwd_hash_params);
	if (ipv6_l3fwd_lookup_struct[socketid] == NULL)
		rte_exit(EXIT_FAILURE, "Unable to create the l3fwd hash on "
				"socket %d\n", socketid);

	/* populate the ipv4 hash */
	for (i = 0; i < RTE_DIM(ipv4_l3fwd_route_array); i++) {
		ret = rte_hash_add_key(ipv4_l3fwd_lookup_struct[socketid],
				(void *)&ipv4_l3fwd_route_array[i].key);
		if (ret < 0) {
			rte_exit(EXIT_FAILURE, "Unable to add entry %u to the"
				"l3fwd hash on socket %d\n", i, socketid);
		}
		ipv4_l3fwd_out_if[ret] = ipv4_l3fwd_route_array[i].if_out;
		printf("Hash: Adding key\n");
		print_ipv4_key(ipv4_l3fwd_route_array[i].key);
	}

	/* populate the ipv6 hash */
	for (i = 0; i < RTE_DIM(ipv6_l3fwd_route_array); i++) {
		ret = rte_hash_add_key(ipv6_l3fwd_lookup_struct[socketid],
				(void *)&ipv6_l3fwd_route_array[i].key);
		if (ret < 0) {
			rte_exit(EXIT_FAILURE, "Unable to add entry %u to the"
				"l3fwd hash on socket %d\n", i, socketid);
		}
		ipv6_l3fwd_out_if[ret] = ipv6_l3fwd_route_array[i].if_out;
		printf("Hash: Adding key\n");
		print_ipv6_key(ipv6_l3fwd_route_array[i].key);
	}
}
#endif

#if (APP_LOOKUP_METHOD == APP_LOOKUP_LPM)
static void
setup_lpm(int socketid)
{
	unsigned i;
	int ret;
	char s[64];

	/* create the LPM table */
	struct rte_lpm_config lpm_ipv4_config;

	lpm_ipv4_config.max_rules = IPV4_L3FWD_LPM_MAX_RULES;
	lpm_ipv4_config.number_tbl8s = 256;
	lpm_ipv4_config.flags = 0;

	snprintf(s, sizeof(s), "IPV4_L3FWD_LPM_%d", socketid);
	ipv4_l3fwd_lookup_struct[socketid] =
		rte_lpm_create(s, socketid, &lpm_ipv4_config);
	if (ipv4_l3fwd_lookup_struct[socketid] == NULL)
		rte_exit(EXIT_FAILURE, "Unable to create the l3fwd LPM table"
				" on socket %d\n", socketid);

	/* populate the LPM table */
	for (i = 0; i < RTE_DIM(ipv4_l3fwd_route_array); i++) {
		ret = rte_lpm_add(ipv4_l3fwd_lookup_struct[socketid],
			ipv4_l3fwd_route_array[i].ip,
			ipv4_l3fwd_route_array[i].depth,
			ipv4_l3fwd_route_array[i].if_out);

		if (ret < 0) {
			rte_exit(EXIT_FAILURE, "Unable to add entry %u to the "
				"l3fwd LPM table on socket %d\n",
				i, socketid);
		}

		printf("LPM: Adding route 0x%08x / %d (%d)\n",
			(unsigned)ipv4_l3fwd_route_array[i].ip,
			ipv4_l3fwd_route_array[i].depth,
			ipv4_l3fwd_route_array[i].if_out);
	}
}
#endif

static int
init_mem(unsigned nb_mbuf)
{
	struct lcore_conf *qconf;
	int socketid;
	unsigned lcore_id;
	char s[64];

	for (lcore_id = 0; lcore_id < RTE_MAX_LCORE; lcore_id++) {
		if (rte_lcore_is_enabled(lcore_id) == 0)
			continue;

		if (numa_on)
			socketid = rte_lcore_to_socket_id(lcore_id);
		else
			socketid = 0;

		if (socketid >= NB_SOCKETS) {
			rte_exit(EXIT_FAILURE, "Socket %d of lcore %u is "
				"out of range %d\n", socketid,
				lcore_id, NB_SOCKETS);
		}
		if (pktmbuf_pool[socketid] == NULL) {
			snprintf(s, sizeof(s), "mbuf_pool_%d", socketid);
			pktmbuf_pool[socketid] =
				rte_pktmbuf_pool_create(s, nb_mbuf,
					MEMPOOL_CACHE_SIZE, 0,
					RTE_MBUF_DEFAULT_BUF_SIZE,
					socketid);
			if (pktmbuf_pool[socketid] == NULL)
				rte_exit(EXIT_FAILURE,
					"Cannot init mbuf pool on socket %d\n",
					socketid);
			else
				printf("Allocated mbuf pool on socket %d\n",
					socketid);

#if (APP_LOOKUP_METHOD == APP_LOOKUP_LPM)
			setup_lpm(socketid);
#else
			setup_hash(socketid);
#endif
		}
		qconf = &lcore_conf[lcore_id];
		qconf->ipv4_lookup_struct = ipv4_l3fwd_lookup_struct[socketid];
#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
		qconf->ipv6_lookup_struct = ipv6_l3fwd_lookup_struct[socketid];
#endif
	}

	return 0;
}

/* Check the link status of all ports in up to 9s, and print them finally */
static void
check_all_ports_link_status(uint32_t port_mask)
{
#define CHECK_INTERVAL 100 /* 100ms */
#define MAX_CHECK_TIME 90 /* 9s (90 * 100ms) in total */
	uint8_t count, all_ports_up, print_flag = 0;
	uint16_t portid;
	struct rte_eth_link link;
	int ret;

	printf("\nChecking link status");
	fflush(stdout);
	for (count = 0; count <= MAX_CHECK_TIME; count++) {
		all_ports_up = 1;
		RTE_ETH_FOREACH_DEV(portid) {
			if ((port_mask & (1 << portid)) == 0)
				continue;
			memset(&link, 0, sizeof(link));
			ret = rte_eth_link_get_nowait(portid, &link);
			if (ret < 0) {
				all_ports_up = 0;
				if (print_flag == 1)
					printf("Port %u link get failed: %s\n",
						portid, rte_strerror(-ret));
				continue;
			}
			/* print link status if flag set */
			if (print_flag == 1) {
				if (link.link_status)
					printf("Port %d Link Up - speed %u "
						"Mbps - %s\n", (uint8_t)portid,
						(unsigned)link.link_speed,
						(link.link_duplex == ETH_LINK_FULL_DUPLEX) ?
						("full-duplex") : ("half-duplex"));
				else
					printf("Port %d Link Down\n",
						(uint8_t)portid);
				continue;
			}
			/* clear all_ports_up flag if any link down */
			if (link.link_status == ETH_LINK_DOWN) {
				all_ports_up = 0;
				break;
			}
		}
		/* after finally printing all link status, get out */
		if (print_flag == 1)
			break;

		if (all_ports_up == 0) {
			printf(".");
			fflush(stdout);
			rte_delay_ms(CHECK_INTERVAL);
		}

		/* set the print_flag if all ports up or timeout */
		if (all_ports_up == 1 || count == (MAX_CHECK_TIME - 1)) {
			print_flag = 1;
			printf("done\n");
		}
	}
}

static int
check_ptype(uint16_t portid)
{
	int i, ret;
	int ptype_l3_ipv4 = 0;
#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
	int ptype_l3_ipv6 = 0;
#endif
	uint32_t ptype_mask = RTE_PTYPE_L3_MASK;

	ret = rte_eth_dev_get_supported_ptypes(portid, ptype_mask, NULL, 0);
	if (ret <= 0)
		return 0;

	uint32_t ptypes[ret];

	ret = rte_eth_dev_get_supported_ptypes(portid, ptype_mask, ptypes, ret);
	for (i = 0; i < ret; ++i) {
		if (ptypes[i] & RTE_PTYPE_L3_IPV4)
			ptype_l3_ipv4 = 1;
#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
		if (ptypes[i] & RTE_PTYPE_L3_IPV6)
			ptype_l3_ipv6 = 1;
#endif
	}

	if (ptype_l3_ipv4 == 0)
		printf("port %d cannot parse RTE_PTYPE_L3_IPV4\n", portid);

#if (APP_LOOKUP_METHOD == APP_LOOKUP_EXACT_MATCH)
	if (ptype_l3_ipv6 == 0)
		printf("port %d cannot parse RTE_PTYPE_L3_IPV6\n", portid);
#endif

#if (APP_LOOKUP_METHOD == APP_LOOKUP_LPM)
	if (ptype_l3_ipv4)
#else /* APP_LOOKUP_EXACT_MATCH */
	if (ptype_l3_ipv4 && ptype_l3_ipv6)
#endif
		return 1;

	return 0;
}

#define TARGET_MIN_FREQ 800   // 800 MHz
#define TARGET_MAX_FREQ 3800  // 3.8 GHz

/* Power Mng Algortimgn heriotic: inspect and print the frequency/turbo capabilities that the power library will use for each lcore. */
static int
configure_power_settings(unsigned int lcore_id, bool enable_turbo)
{
	int ret;
	uint32_t freqs[32];
	uint32_t num_freqs;
	(void)enable_turbo;

	/* Get and print available frequencies for the lcore */
	num_freqs = rte_power_freqs(lcore_id, freqs, RTE_DIM(freqs));
	if (num_freqs == 0) {
		RTE_LOG(WARNING, L3FWD_POWER,
			"No available frequencies for lcore %u\n", lcore_id);
	} else {
		RTE_LOG(INFO, L3FWD_POWER, "Available frequencies for lcore %u:\n", lcore_id);
		for (uint32_t i = 0; i < num_freqs; i++) {
			RTE_LOG(INFO, L3FWD_POWER, "  Frequency %u: %u kHz\n", i, freqs[i]);
		}
	}

	/* Query the turbo status for the given lcore */
	ret = rte_power_turbo_status(lcore_id);
	if (ret < 0) {
		RTE_LOG(ERR, POWER, "Failed to retrieve turbo status for lcore %u\n", lcore_id);
		return -1;
	}

	/* Print the turbo status */
	if (ret == 1) {
		printf("Turbo Boost is ENABLED for lcore %u\n", lcore_id);
	} else if (ret == 0) {
		printf("Turbo Boost is DISABLED for lcore %u\n", lcore_id);
	} else {
		RTE_LOG(ERR, POWER, "Unexpected turbo status value for lcore %u: %d\n", lcore_id, ret);
	}

	return 0;
}

/* Power Mng Algortimgn heriotic: initialize the DPDK power library on each enabled lcore so the legacy and empty-poll modes can control CPU frequency. */
static int
init_power_library(void)
{
	enum power_management_env env;
	unsigned int lcore_id;
	int ret = 0;

	RTE_LCORE_FOREACH(lcore_id) {
		/* init power management library */
		ret = rte_power_init(lcore_id);
		if (ret) {
			RTE_LOG(ERR, POWER,
				"Library initialization failed on core %u\n",
				lcore_id);
			return ret;
		}

		ret = configure_power_settings(lcore_id, 1);
		if (ret) {
			RTE_LOG(ERR, POWER,
				"config min/max/turbo failed %u\n",
				lcore_id);
			return ret;
		}

		/* we're not supporting the VM channel mode */
		env = rte_power_get_env();
		if (env != PM_ENV_ACPI_CPUFREQ &&
				env != PM_ENV_PSTATE_CPUFREQ) {
			RTE_LOG(ERR, POWER,
				"Only ACPI and PSTATE mode are supported\n");
			return -1;
		}
	}
	return ret;
}

/* Power Mng Algortimgn heriotic: release the power library state for every lcore when the application exits. */
static int
deinit_power_library(void)
{
	unsigned int lcore_id;
	int ret = 0;

	RTE_LCORE_FOREACH(lcore_id) {
		/* deinit power management library */
		ret = rte_power_exit(lcore_id);
		if (ret) {
			RTE_LOG(ERR, POWER,
				"Library deinitialization failed on core %u\n",
				lcore_id);
			return ret;
		}
	}
	return ret;
}

static void
get_current_stat_values(uint64_t *values)
{
	unsigned int lcore_id = rte_lcore_id();
	struct lcore_conf *qconf;
	uint64_t app_eps = 0, app_fps = 0, app_br = 0;
	uint64_t count = 0;

	RTE_LCORE_FOREACH_SLAVE(lcore_id) {
		qconf = &lcore_conf[lcore_id];
		if (qconf->n_rx_queue == 0)
			continue;
		count++;
		rte_spinlock_lock(&stats[lcore_id].telemetry_lock);
		app_eps += stats[lcore_id].ep_nep[1];
		app_fps += stats[lcore_id].fp_nfp[1];
		app_br += stats[lcore_id].br;
		rte_spinlock_unlock(&stats[lcore_id].telemetry_lock);
	}

	if (count > 0) {
		values[0] = app_eps / count;
		values[1] = app_fps / count;
		values[2] = app_br / count;
	} else
		memset(values, 0, sizeof(uint64_t) * NUM_TELSTATS);
}

static void
update_telemetry(__rte_unused struct rte_timer *tim,
		__rte_unused void *arg)
{
	int ret;
	uint64_t values[NUM_TELSTATS] = {0};

	get_current_stat_values(values);
	ret = rte_metrics_update_values(RTE_METRICS_GLOBAL, telstats_index,
					values, RTE_DIM(values));
	if (ret < 0)
		RTE_LOG(WARNING, POWER, "failed to update metrcis\n");
}

static int
handle_app_stats(const char *cmd __rte_unused,
		const char *params __rte_unused,
		struct rte_tel_data *d)
{
	uint64_t values[NUM_TELSTATS] = {0};
	uint32_t i;

	rte_tel_data_start_dict(d);
	get_current_stat_values(values);
	for (i = 0; i < NUM_TELSTATS; i++)
		rte_tel_data_add_dict_u64(d, telstats_strings[i].name,
				values[i]);
	return 0;
}

static void
telemetry_setup_timer(void)
{
	int lcore_id = rte_lcore_id();
	uint64_t hz = rte_get_timer_hz();
	uint64_t ticks;

	ticks = hz / TELEMETRY_INTERVALS_PER_SEC;
	rte_timer_reset_sync(&telemetry_timer,
			ticks,
			PERIODICAL,
			lcore_id,
			update_telemetry,
			NULL);
}

static void
empty_poll_setup_timer(void)
{
	int lcore_id = rte_lcore_id();
	uint64_t hz = rte_get_timer_hz();

	struct ep_params *ep_ptr = ep_params;

	ep_ptr->interval_ticks = hz / INTERVALS_PER_SECOND;

	rte_timer_reset_sync(&ep_ptr->timer0,
			ep_ptr->interval_ticks,
			PERIODICAL,
			lcore_id,
			rte_empty_poll_detection,
			(void *)ep_ptr);
}

static int
launch_timer(unsigned int lcore_id)
{
	int64_t prev_tsc = 0, cur_tsc, diff_tsc, cycles_10ms;

	RTE_SET_USED(lcore_id);

	if (rte_get_master_lcore() != lcore_id) {
		rte_panic("timer on lcore:%d which is not master core:%d\n",
				lcore_id,
				rte_get_master_lcore());
	}

	RTE_LOG(INFO, POWER, "Bring up the Timer\n");

	if (app_mode == APP_MODE_EMPTY_POLL)
		empty_poll_setup_timer();
	else
		telemetry_setup_timer();

	cycles_10ms = rte_get_timer_hz() / 100;

	while (!is_done()) {
		cur_tsc = rte_rdtsc();
		diff_tsc = cur_tsc - prev_tsc;
		if (diff_tsc > cycles_10ms) {
			rte_timer_manage();
			prev_tsc = cur_tsc;
			cycles_10ms = rte_get_timer_hz() / 100;
		}
	}

	RTE_LOG(INFO, POWER, "Timer_subsystem is done\n");

	return 0;
}

static int
autodetect_mode(void)
{
	RTE_LOG(NOTICE, L3FWD_POWER, "Operating mode not specified, probing frequency scaling support...\n");

	/*
	 * Empty poll and telemetry modes have to be specifically requested to
	 * be enabled, but we can auto-detect between interrupt mode with or
	 * without frequency scaling. Both ACPI and pstate can be used.
	 */
	if (rte_power_check_env_supported(PM_ENV_ACPI_CPUFREQ))
		return APP_MODE_LEGACY;
	if (rte_power_check_env_supported(PM_ENV_PSTATE_CPUFREQ))
		return APP_MODE_LEGACY;

	RTE_LOG(NOTICE, L3FWD_POWER, "Frequency scaling not supported, selecting interrupt-only mode\n");

	return APP_MODE_INTERRUPT;
}

static const char *
mode_to_str(enum appmode mode)
{
	switch (mode) {
	case APP_MODE_LEGACY:
		return "legacy";
	case APP_MODE_EMPTY_POLL:
		return "empty poll";
	case APP_MODE_TELEMETRY:
		return "telemetry";
	case APP_MODE_INTERRUPT:
		return "interrupt-only";
	default:
		return "invalid";
	}
}

//**********************************************************************

static int
my_init_mem(void)
{
	buffer_table = rte_hash_create(&ut_params);
	if (buffer_table == NULL) {
		printf("UNABLE TO CREATE HASHTABLE\n");
		rte_exit(EXIT_FAILURE, "UNABLE TO CREATE HASHTABLE\n");
	}

	return 0;
}

//**********************************************************************

int
main(int argc, char **argv)
{
	struct lcore_conf *qconf;
	struct rte_eth_dev_info dev_info;
	struct rte_eth_txconf *txconf;
	int ret;
	uint16_t nb_ports;
	uint16_t queueid;
	unsigned lcore_id;
	uint64_t hz;
	uint32_t n_tx_queue, nb_lcores;
	uint32_t dev_rxq_num, dev_txq_num;
	uint8_t nb_rx_queue, queue, socketid;
	uint16_t portid;
	const char *ptr_strings[NUM_TELSTATS];

	/* catch SIGINT and restore cpufreq governor to ondemand */
	signal(SIGINT, signal_exit_now);

	/* Add Your Tracing Instrumentation Here: parse --trace-dir early from argv so the later anchor writer knows where to store anchor.txt. */
	capture_trace_dir_from_argv(argc, argv);

	/* init EAL */
	ret = rte_eal_init(argc, argv);
	if (ret < 0)
		rte_exit(EXIT_FAILURE, "Invalid EAL parameters\n");
	argc -= ret;
	argv += ret;

	/* init RTE timer library to be used late */
	rte_timer_subsystem_init();

	/* parse application arguments (after the EAL ones) */
	ret = parse_args(argc, argv);
	if (ret < 0)
		rte_exit(EXIT_FAILURE, "Invalid L3FWD parameters\n");

	/* Add Your Tracing Instrumentation Here: initialize runtime trace gating after arguments are parsed and before worker loops start polling. */
	trace_runtime_init();

	if (app_mode == APP_MODE_DEFAULT)
		app_mode = autodetect_mode();

	RTE_LOG(INFO, L3FWD_POWER, "Selected operation mode: %s\n",
		mode_to_str(app_mode));

	/* only legacy and empty poll mode rely on power library */
	if ((app_mode == APP_MODE_LEGACY || app_mode == APP_MODE_EMPTY_POLL) &&
			/* Power Mng Algortimgn heriotic: bring up the power library only for the modes that actively control CPU frequency. */
			init_power_library())
		rte_exit(EXIT_FAILURE, "init_power_library failed\n");

	if (update_lcore_params() < 0)
		rte_exit(EXIT_FAILURE, "update_lcore_params failed\n");

	if (check_lcore_params() < 0)
		rte_exit(EXIT_FAILURE, "check_lcore_params failed\n");

	ret = init_lcore_rx_queues();
	if (ret < 0)
		rte_exit(EXIT_FAILURE, "init_lcore_rx_queues failed\n");

	nb_ports = rte_eth_dev_count_avail();

	if (check_port_config() < 0)
		rte_exit(EXIT_FAILURE, "check_port_config failed\n");

	nb_lcores = rte_lcore_count();

	// disabling or enalbing the downscaling freq timer
	if (app_mode == APP_MODE_LEGACY) {
		/* init timer structures for each enabled lcore */
		/* Power Mng Algortimgn heriotic: create and arm the background timer used by the legacy mode for periodic frequency down-scaling decisions. */
		rte_timer_init(&power_timers[lcore_id]);
		hz = rte_get_timer_hz();
		rte_timer_reset(&power_timers[lcore_id],
				hz/TIMER_NUMBER_PER_SECOND,
				SINGLE, lcore_id,
				power_timer_cb, NULL);
	}

	/* initialize all ports */
	RTE_ETH_FOREACH_DEV(portid) {
		struct rte_eth_conf local_port_conf = port_conf;
		/* not all app modes need interrupts */
		bool need_intr = app_mode == APP_MODE_LEGACY ||
				app_mode == APP_MODE_INTERRUPT;

		/* skip ports that are not enabled */
		if ((enabled_port_mask & (1 << portid)) == 0) {
			printf("\nSkipping disabled port %d\n", portid);
			continue;
		}

		/* init port */
		printf("Initializing port %d ... ", portid);
		fflush(stdout);

		ret = rte_eth_dev_info_get(portid, &dev_info);
		if (ret != 0)
			rte_exit(EXIT_FAILURE,
				"Error during getting device (port %u) info: %s\n",
				portid, strerror(-ret));

		dev_rxq_num = dev_info.max_rx_queues;
		dev_txq_num = dev_info.max_tx_queues;

		nb_rx_queue = get_port_n_rx_queues(portid);
		if (nb_rx_queue > dev_rxq_num)
			rte_exit(EXIT_FAILURE,
				"Cannot configure not existed rxq: "
				"port=%d\n", portid);

		n_tx_queue = nb_lcores;
		if (n_tx_queue > dev_txq_num)
			n_tx_queue = dev_txq_num;
		printf("Creating queues: nb_rxq=%d nb_txq=%u... ",
			nb_rx_queue, (unsigned)n_tx_queue);
		/* If number of Rx queue is 0, no need to enable Rx interrupt */
		if (nb_rx_queue == 0)
			need_intr = false;

		if (need_intr)
			local_port_conf.intr_conf.rxq = 1;

		ret = rte_eth_dev_info_get(portid, &dev_info);
		if (ret != 0)
			rte_exit(EXIT_FAILURE,
				"Error during getting device (port %u) info: %s\n",
				portid, strerror(-ret));

		if (dev_info.tx_offload_capa & DEV_TX_OFFLOAD_MBUF_FAST_FREE)
			local_port_conf.txmode.offloads |=
				DEV_TX_OFFLOAD_MBUF_FAST_FREE;

		local_port_conf.rx_adv_conf.rss_conf.rss_hf &=
			dev_info.flow_type_rss_offloads;
		if (local_port_conf.rx_adv_conf.rss_conf.rss_hf !=
				port_conf.rx_adv_conf.rss_conf.rss_hf) {
			printf("Port %u modified RSS hash function based on hardware support,"
				"requested:%#" PRIx64 " configured:%#" PRIx64 "\n",
				portid,
				port_conf.rx_adv_conf.rss_conf.rss_hf,
				local_port_conf.rx_adv_conf.rss_conf.rss_hf);
		}

		ret = rte_eth_dev_configure(portid, nb_rx_queue,
					(uint16_t)n_tx_queue, &local_port_conf);
		if (ret < 0)
			rte_exit(EXIT_FAILURE, "Cannot configure device: "
					"err=%d, port=%d\n", ret, portid);

		ret = rte_eth_dev_adjust_nb_rx_tx_desc(portid, &nb_rxd,
						       &nb_txd);
		if (ret < 0)
			rte_exit(EXIT_FAILURE,
				 "Cannot adjust number of descriptors: err=%d, port=%d\n",
				 ret, portid);

		ret = rte_eth_macaddr_get(portid, &ports_eth_addr[portid]);
		if (ret < 0)
			rte_exit(EXIT_FAILURE,
				 "Cannot get MAC address: err=%d, port=%d\n",
				 ret, portid);

		print_ethaddr(" Address:", &ports_eth_addr[portid]);
		printf(", ");

		/* init memory */
		unsigned int nb_mbufs;
		nb_mbufs = 1000000U;
		ret = init_mem(nb_mbufs);
		if (ret < 0)
			rte_exit(EXIT_FAILURE, "init_mem failed\n");

		for (lcore_id = 0; lcore_id < RTE_MAX_LCORE; lcore_id++) {
			if (rte_lcore_is_enabled(lcore_id) == 0)
				continue;

			/* Initialize TX buffers */
			qconf = &lcore_conf[lcore_id];
			qconf->tx_buffer[portid] = rte_zmalloc_socket("tx_buffer",
				RTE_ETH_TX_BUFFER_SIZE(MAX_PKT_BURST), 0,
				rte_eth_dev_socket_id(portid));
			if (qconf->tx_buffer[portid] == NULL)
				rte_exit(EXIT_FAILURE, "Can't allocate tx buffer for port %u\n",
					 portid);

			rte_eth_tx_buffer_init(qconf->tx_buffer[portid], MAX_PKT_BURST);
		}

		/* init one TX queue per couple (lcore,port) */
		queueid = 0;
		for (lcore_id = 0; lcore_id < RTE_MAX_LCORE; lcore_id++) {
			if (rte_lcore_is_enabled(lcore_id) == 0)
				continue;

			if (queueid >= dev_txq_num)
				continue;

			if (numa_on)
				socketid =
					(uint8_t)rte_lcore_to_socket_id(lcore_id);
			else
				socketid = 0;

			txconf = &dev_info.default_txconf;
			txconf->offloads = local_port_conf.txmode.offloads;
			ret = rte_eth_tx_queue_setup(portid, queueid, nb_txd,
						     socketid, txconf);
			if (ret < 0)
				rte_exit(EXIT_FAILURE,
					"rte_eth_tx_queue_setup: err=%d, "
					"port=%d\n", ret, portid);

			queueid++;
			qconf->n_tx_port++;
		}
		printf("\n");
	}

	for (lcore_id = 0; lcore_id < RTE_MAX_LCORE; lcore_id++) {
		if (rte_lcore_is_enabled(lcore_id) == 0)
			continue;

		max_used_lcore++;

		qconf = &lcore_conf[lcore_id];
		fflush(stdout);

		/* init RX queues */
		for (queue = 0; queue < qconf->n_rx_queue; ++queue) {
			struct rte_eth_rxconf rxq_conf;

			portid = qconf->rx_queue_list[queue].port_id;
			queueid = qconf->rx_queue_list[queue].queue_id;

			if (numa_on)
				socketid =
					(uint8_t)rte_lcore_to_socket_id(lcore_id);
			else
				socketid = 0;

			printf(
				"\n------------------------------------------\n"
				"lcore_id, portid, queueid, socketid, rxq=%d,%d,%d,%d\n"
				"------------------------------------------\n"
				"lcore_id, portid, queueid, socketid, txq=%u,%u,%d,%d\n"
				"------------------------------------------\n",
				lcore_id, portid, queueid, socketid,
				lcore_id, portid, queueid, socketid
			);
			fflush(stdout);

			qconf->tx_queue_id[portid] = queueid;
			qconf->tx_port_id[qconf->n_tx_port] = portid;

			ret = rte_eth_dev_info_get(portid, &dev_info);
			if (ret != 0)
				rte_exit(EXIT_FAILURE,
					"Error during getting device (port %u) info: %s\n",
					portid, strerror(-ret));

			rxq_conf = dev_info.default_rxconf;
			rxq_conf.offloads = port_conf.rxmode.offloads;
			ret = rte_eth_rx_queue_setup(portid, queueid, nb_rxd,
				socketid, &rxq_conf,
				pktmbuf_pool[socketid]);
			if (ret < 0)
				rte_exit(EXIT_FAILURE,
					"rte_eth_rx_queue_setup: err=%d, "
					"port=%d\n", ret, portid);

			if (parse_ptype) {
				if (add_cb_parse_ptype(portid, queueid) < 0)
					rte_exit(EXIT_FAILURE,
						 "Fail to add ptype cb\n");
			} else if (!check_ptype(portid))
				rte_exit(EXIT_FAILURE,
					 "PMD can not provide needed ptypes\n");
		}
	}

	printf("\n");

	/* start ports */
	RTE_ETH_FOREACH_DEV(portid) {
		if ((enabled_port_mask & (1 << portid)) == 0) {
			continue;
		}
		/* Start device */
		ret = rte_eth_dev_start(portid);
		if (ret < 0)
			rte_exit(EXIT_FAILURE, "rte_eth_dev_start: err=%d, "
						"port=%d\n", ret, portid);
		/*
		 * If enabled, put device in promiscuous mode.
		 * This allows IO forwarding mode to forward packets
		 * to itself through 2 cross-connected  ports of the
		 * target machine.
		 */
		if (promiscuous_on) {
			ret = rte_eth_promiscuous_enable(portid);
			if (ret != 0)
				rte_exit(EXIT_FAILURE,
					"rte_eth_promiscuous_enable: err=%s, port=%u\n",
					rte_strerror(-ret), portid);
		}
		/* initialize spinlock for each port */
		rte_spinlock_init(&(locks[portid]));
	}

	check_all_ports_link_status(enabled_port_mask);

	//**********************************************************************

	/* init memory */
	ret = my_init_mem();
	if (ret < 0)
		rte_exit(EXIT_FAILURE, "init_mem failed :err=%d\n", ret);

	printf("Max used lcore: %u\n", max_used_lcore);
	char pool_name[32];
	int i;
	//Create NUM_POOLS mbuf pools
	for (i = 0; i < (int)max_used_lcore; i++) {
		snprintf(pool_name, sizeof(pool_name), "clone_pool_%d", i);
		my_clone_pool[i] = rte_pktmbuf_pool_create(
			pool_name,                 // Pool name
			CLONE_POOL_SIZE,          // Number of mbufs
			MEMPOOL_CACHE_SIZE,       // Cache size
			0,                        // Additional size
			RTE_MBUF_DEFAULT_BUF_SIZE,// Buffer size
			rte_socket_id());         // Socket ID for NUMA awareness

		if (my_clone_pool[i] == NULL)
			rte_exit(EXIT_FAILURE, "Cannot init clone pool %d\n", i);

		printf("Initialized mbuf pool %d: %s\n", i, pool_name);
	}

	//Pre-allocate mbufs from the clone pool
	for (int j = 0; j < (int)max_used_lcore; j++) {
		for (int i2 = 0; i2 < CLONE_POOL_SIZE; i2++) {
			clone_mbufs[j][i2] = rte_pktmbuf_alloc(my_clone_pool[j]);
			if (clone_mbufs[j][i2] == NULL) {
				rte_exit(EXIT_FAILURE, "Failed to pre-allocate mbufs for cloning\n");
			}
		}
	}

	populate_teid_qfi_table("/home/admin/mohsen/dpdk/examples/l3fwd-power/config_table1_64000_0_table2_10.0.0.0_10.0.250.0_0.txt");
	//populate_ul_teid_table("/home/dv/mohsen/dpdk20/dpdk/examples/gnb/config_table1_64000_0_table2_10.0.0.0_10.0.250.0_0.txt");

	// Print total number of entries in each hash table
	if (teid_qfi_table != NULL) {
		printf("Total number of entries in teid_qfi_table: %u\n", rte_hash_count(teid_qfi_table));
	} else {
		printf("teid_qfi_table not initialized.\n");
	}

	//**********************************************************************

	if (app_mode == APP_MODE_EMPTY_POLL) {
		if (empty_poll_train) {
			policy.state = TRAINING;
		} else {
			policy.state = MED_NORMAL;
			policy.med_base_edpi = ep_med_edpi;
			policy.hgh_base_edpi = ep_hgh_edpi;
		}

		ret = rte_power_empty_poll_stat_init(&ep_params,
				freq_tlb,
				&policy);
		if (ret < 0)
			rte_exit(EXIT_FAILURE, "empty poll init failed");
	}

	/* launch per-lcore init on every lcore */
	if (app_mode == APP_MODE_LEGACY) {
		/* Power Mng Algortimgn heriotic: launch the legacy worker loop that combines RX polling with per-poll scaling hints and timer-driven down-scaling. */
		rte_eal_mp_remote_launch(main_legacy_loop, NULL, CALL_MASTER);
	} else if (app_mode == APP_MODE_EMPTY_POLL) {
		empty_poll_stop = false;
		rte_eal_mp_remote_launch(main_empty_poll_loop, NULL,
				SKIP_MASTER);
	} else if (app_mode == APP_MODE_TELEMETRY) {
		unsigned int i3;

		/* Init metrics library */
		rte_metrics_init(rte_socket_id());
		/** Register stats with metrics library */
		for (i3 = 0; i3 < NUM_TELSTATS; i3++)
			ptr_strings[i3] = telstats_strings[i3].name;

		ret = rte_metrics_reg_names(ptr_strings, NUM_TELSTATS);
		if (ret >= 0)
			telstats_index = ret;
		else
			rte_exit(EXIT_FAILURE, "failed to register metrics names");

		RTE_LCORE_FOREACH_SLAVE(lcore_id) {
			rte_spinlock_init(&stats[lcore_id].telemetry_lock);
		}
		rte_timer_init(&telemetry_timer);
		rte_telemetry_register_cmd("/l3fwd-power/stats",
				handle_app_stats,
				"Returns global power stats. Parameters: None");
		rte_eal_mp_remote_launch(main_telemetry_loop, NULL,
						SKIP_MASTER);
	} else if (app_mode == APP_MODE_INTERRUPT) {
		rte_eal_mp_remote_launch(main_intr_loop, NULL, CALL_MASTER);
	}

	if (app_mode == APP_MODE_EMPTY_POLL || app_mode == APP_MODE_TELEMETRY)
		launch_timer(rte_lcore_id());

	RTE_LCORE_FOREACH_SLAVE(lcore_id) {
		if (rte_eal_wait_lcore(lcore_id) < 0)
			return -1;
	}

	RTE_ETH_FOREACH_DEV(portid) {
		if ((enabled_port_mask & (1 << portid)) == 0)
			continue;

		rte_eth_dev_stop(portid);
		rte_eth_dev_close(portid);
	}

	if (app_mode == APP_MODE_EMPTY_POLL)
		rte_power_empty_poll_stat_free();

	if ((app_mode == APP_MODE_LEGACY || app_mode == APP_MODE_EMPTY_POLL) &&
			/* Power Mng Algortimgn heriotic: shut down the power library after worker loops stop so CPU control state is cleaned up properly. */
			deinit_power_library())
		rte_exit(EXIT_FAILURE, "deinit_power_library failed\n");

	if (rte_eal_cleanup() < 0)
		RTE_LOG(ERR, L3FWD_POWER, "EAL cleanup failed\n");

	return 0;
}
