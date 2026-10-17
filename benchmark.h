/* Medição do circuito gerado. Linux x86-64 / GCC. Nenhum tempo de compilação
 * ou de abertura do processo participa dos tempos medidos abaixo. */
#include <cpuid.h>
#include <errno.h>
#include <linux/perf_event.h>
#include <sched.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static volatile float lab_sink;

static uint64_t lab_time(clockid_t clock_id) {
    struct timespec now;
    clock_gettime(clock_id, &now);
    return (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec;
}

static int lab_has_rdtscp(void) {
    unsigned a, b, c, d;
    return __get_cpuid(0x80000001, &a, &b, &c, &d) && (d & (1u << 27));
}

static uint64_t lab_timestamp(unsigned *aux) {
    _mm_lfence();
    uint64_t ticks = __rdtscp(aux);
    _mm_lfence();
    return ticks;
}

static int lab_perf(void) {
    struct perf_event_attr event = {0};
    event.type = PERF_TYPE_HARDWARE;
    event.size = sizeof(event);
    event.config = PERF_COUNT_HW_CPU_CYCLES;
    event.disabled = 1;
    event.exclude_kernel = 1;
    event.exclude_hv = 1;
    event.read_format = PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
    return (int)syscall(__NR_perf_event_open, &event, 0, -1, -1, 0);
}

static __attribute__((noinline, noipa)) float lab_empty(const float *x) {
    __asm__ volatile("" ::: "memory");
    return x[0];
}

static void lab_benchmark(int repetitions, int samples, int warmup, int core,
                          const int8_t *weights, const float *x, float scale) {
    int affinity_error = 0;
    if (core >= 0) {
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(core, &set);
        if (sched_setaffinity(0, sizeof(set), &set) != 0) affinity_error = errno;
    }
    int perf_fd = lab_perf();
    int perf_error = perf_fd < 0 ? errno : 0;
    int has_tsc = lab_has_rdtscp();
    for (int i = 0; i < warmup; i++) lab_sink = circuit(0, weights, x, scale);

    printf("{\"benchmark\":{\"repetitions\":%d,\"warmup\":%d,\"core\":%d,\"affinityErrno\":%d,\"perfErrno\":%d,\"hasTsc\":%s,\"samples\":[",
           repetitions, warmup, sched_getcpu(), affinity_error, perf_error, has_tsc ? "true" : "false");
    for (int sample = 0; sample < samples; sample++) {
        unsigned aux0 = 0, aux1 = 0;
        if (perf_fd >= 0) {
            ioctl(perf_fd, PERF_EVENT_IOC_RESET, 0);
            ioctl(perf_fd, PERF_EVENT_IOC_ENABLE, 0);
        }
        uint64_t cpu0 = lab_time(CLOCK_PROCESS_CPUTIME_ID);
        uint64_t wall0 = lab_time(CLOCK_MONOTONIC_RAW);
        uint64_t tick0 = has_tsc ? lab_timestamp(&aux0) : 0;
        for (int i = 0; i < repetitions; i++) lab_sink = circuit(0, weights, x, scale);
        uint64_t tick1 = has_tsc ? lab_timestamp(&aux1) : 0;
        uint64_t wall1 = lab_time(CLOCK_MONOTONIC_RAW);
        uint64_t cpu1 = lab_time(CLOCK_PROCESS_CPUTIME_ID);
        uint64_t event[3] = {0, 0, 0};
        int perf_read = 0;
        if (perf_fd >= 0) {
            ioctl(perf_fd, PERF_EVENT_IOC_DISABLE, 0);
            perf_read = read(perf_fd, event, sizeof(event)) == sizeof(event);
        }
        uint64_t empty0 = lab_time(CLOCK_MONOTONIC_RAW);
        for (int i = 0; i < repetitions; i++) lab_sink = lab_empty(x);
        uint64_t empty1 = lab_time(CLOCK_MONOTONIC_RAW);
        if (sample) putchar(',');
        printf("{\"wallNs\":%llu,\"cpuNs\":%llu,\"tscTicks\":%llu,\"migrated\":%s,\"emptyNs\":%llu,\"perfRead\":%s,\"cycles\":%llu,\"enabledNs\":%llu,\"runningNs\":%llu}",
               (unsigned long long)(wall1-wall0), (unsigned long long)(cpu1-cpu0),
               (unsigned long long)(tick1-tick0), aux0 != aux1 ? "true" : "false",
               (unsigned long long)(empty1-empty0), perf_read ? "true" : "false",
               (unsigned long long)event[0], (unsigned long long)event[1], (unsigned long long)event[2]);
    }
    printf("],\"sink\":%.9g}}\n", lab_sink);
    if (perf_fd >= 0) close(perf_fd);
}
