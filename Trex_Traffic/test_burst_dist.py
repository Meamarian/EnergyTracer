#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import ipaddress

from scapy.all import Ether, IP, UDP, Raw, Packet, BitField
from trex_stl_lib.api import STLStream, STLTXSingleBurst, STLPktBuilder


# ------------------ custom GTP-U header ------------------

class GTPULayer(Packet):
    name = "GTPULayer"
    fields_desc = [
        BitField("flags", 0, 8),
        BitField("type", 0, 8),
        BitField("length", 0, 16),
        BitField("teid", 0, 32),
        BitField("seq_num", 0, 16),
        BitField("qfi", 0, 8),
    ]


# ------------------ helpers ------------------

def shard_to_ip(shard: int) -> str:
    """
    Return an IPv4 address whose integer value is exactly 'shard'.

    This is intentional:
      int(IPv4Address(shard_to_ip(k))) % N == k   for 0 <= k < N

    So if your P4 does:
      hdr.ipv4.dstAddr % N
    then shard k maps exactly to interface k.

    Examples:
      0 -> 0.0.0.0
      1 -> 0.0.0.1
      7 -> 0.0.0.7
    """
    return str(ipaddress.IPv4Address(shard))


def build_gtp_packet(total_size: int, dst_ip: str, src_ip: str = "1.0.0.1"):
    """
    Build one GTP-like packet and pad it to total_size bytes.
    """
    pkt = (
        Ether(dst="00:15:4d:13:2e:13", src="00:15:4d:13:00:00")
        / IP(src=src_ip, dst=dst_ip, ttl=64)
        / UDP(sport=2152, dport=2152)
        / GTPULayer(flags=0x01, type=0xff, length=32, teid=0, seq_num=0, qfi=0)
        / IP(src=src_ip, dst=dst_ip, ttl=64)
        / UDP(sport=2153, dport=2152)
    )

    pad_len = max(0, total_size - len(pkt))
    if pad_len:
        pkt = pkt / Raw(b"x" * pad_len)

    return pkt


def build_packet_builders(pktsize: int, distribute: int):
    """
    Pre-build one packet template per shard/interface.
    """
    builders = []
    for shard in range(distribute):
        dst_ip = shard_to_ip(shard)
        pkt = build_gtp_packet(pktsize, dst_ip=dst_ip)
        builders.append(STLPktBuilder(pkt=pkt))
    return builders


def build_schedule(pps_mpps: float, burst_us: int, gap_ms: float, bursts: int, start_delay_us: float):
    """
    Build a burst schedule where --pps is interpreted as MPPS.

    Examples:
      pps_mpps = 1.0  -> 1 packet every 1 us
      pps_mpps = 0.5  -> 1 packet every 2 us
      pps_mpps = 2.0  -> 1 packet every 0.5 us

    burst_us keeps its meaning as burst duration in microseconds.
    gap_ms keeps its meaning as gap between bursts in milliseconds.
    """
    if pps_mpps <= 0:
        raise ValueError("--pps must be > 0")

    pps = pps_mpps * 1_000_000.0
    interval_us = 1_000_000.0 / pps
    gap_us = gap_ms * 1000.0

    schedule = []
    pkt_idx = 0

    for burst_idx in range(bursts):
        base_t = start_delay_us + burst_idx * (burst_us + gap_us)
        t_us = 0.0

        while t_us < float(burst_us):
            schedule.append((pkt_idx, base_t + t_us))
            pkt_idx += 1
            t_us += interval_us

    return schedule


def build_streams(args):
    packet_builders = build_packet_builders(args.pktsize, args.distribute)
    schedule = build_schedule(
        pps_mpps=args.pps,
        burst_us=args.burst_us,
        gap_ms=args.gap_ms,
        bursts=args.bursts,
        start_delay_us=args.start_delay_us,
    )

    if not schedule:
        return []

    streams = []

    for i, (pkt_idx, t_us) in enumerate(schedule):
        shard = pkt_idx % args.distribute
        packet = packet_builders[shard]

        if i == 0:
            self_start = True
            isg = float(t_us)
        else:
            self_start = False
            prev_t_us = schedule[i - 1][1]
            isg = float(t_us - prev_t_us)

        next_name = None if i == len(schedule) - 1 else f"s{i + 1}"

        streams.append(
            STLStream(
                name=f"s{i}",
                self_start=self_start,
                isg=isg,
                packet=packet,
                mode=STLTXSingleBurst(total_pkts=1, percentage=100),
                next=next_name,
            )
        )

    print(
        f"[profile] packets={len(schedule)}, "
        f"pps={args.pps} Mpps, "
        f"burst_us={args.burst_us}, "
        f"gap_ms={args.gap_ms}, "
        f"bursts={args.bursts}, "
        f"distribute={args.distribute}"
    )

    for shard in range(args.distribute):
        print(f"[profile] shard {shard} -> dst_ip {shard_to_ip(shard)}")

    return streams


def get_streams(direction=0, **kwargs):
    """
    TRex entry point.

    Example:
      start -f stl/test_burst.py -p 0 -d 15 -t --pktsize 128 --bursty --pps 0.5 --burst_us 300 --gap_ms 100 --bursts 40 --dist 8
    """
    parser = argparse.ArgumentParser(add_help=False)

    parser.add_argument("--pktsize", type=int, default=128)
    parser.add_argument("--burst_us", type=int, default=300)
    parser.add_argument("--gap_ms", type=float, default=100.0)
    parser.add_argument("--bursts", type=int, default=40)
    parser.add_argument("--start_delay_us", type=float, default=0.0)
    parser.add_argument("--distribute", "--dist", dest="distribute",
                        type=int, choices=range(1, 9), default=1)

    # --pps is interpreted as MPPS to preserve your old usage style:
    #   --pps 1.0  -> 1 Mpps
    #   --pps 0.5  -> 0.5 Mpps
    parser.add_argument("--pps", type=float, default=1.0)
    parser.add_argument("--bursty", action="store_true", default=False)

    tunables = kwargs.get("tunables", ())
    if isinstance(tunables, str):
        tunables = tunables.split()

    args, _ = parser.parse_known_args(list(tunables))

    # optional direct overrides
    for k, v in kwargs.items():
        if k == "tunables":
            continue
        key = k.replace("-", "_")
        if hasattr(args, key):
            setattr(args, key, v)

    return build_streams(args)


class _ProfileWrapper(object):
    def get_streams(self, direction=0, **kwargs):
        return get_streams(direction=direction, **kwargs)


def register():
    return _ProfileWrapper()


if __name__ == "__main__":
    print("Load this file from TRex with -f ... -t ...")
