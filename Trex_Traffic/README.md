
# Traffic Generation Profile

This profile generates bursty GTP-like UDP traffic for **TRex Stateless (STL)**.
It is intended to be loaded by TRex as a Python traffic profile, not run directly as a normal shell script.

---

## Overview

The profile:

- builds a custom outer GTP-U-like header,
- wraps an inner IPv4/UDP packet,
- pads each packet to a user-selected total size,
- generates bursts separated by a configurable idle gap,
- uses `--pps` to control the packet rate **inside each burst**, and
- distributes packets across multiple destination-IP shards so traffic can be steered across multiple interfaces.

The distribution logic is intentional: shard `k` is mapped to an IPv4 destination address whose integer value is exactly `k`. This is useful when the forwarding logic hashes or selects output interfaces using `dstAddr % N`.

---

## Why this profile uses one packet per stream

This profile builds the traffic pattern as a chain of **single-packet TRex streams**. Each packet is scheduled with its own inter-stream gap, so the burst shape is introduced explicitly packet by packet.

This design is intentional. In our validation with **EnergyTracer**, this approach produced a more accurate burst introduction pattern than coarser TRex traffic-generation methods. For example, a run that prints:

```text
Attaching 10000 streams to port(s) [0._]: [SUCCESS]
```

means that TRex attached **10000 one-packet streams**, and together those streams form the requested burst schedule. In that case, the 10000 streams are not an error; they are the burst pattern itself.

---

## File placement

Place the profile in the TRex STL profiles area, for example:

```text
<TRex>/scripts/stl/test_burst_dist.py
```

Any accessible path can be used, but keeping the file under `scripts/stl/` is the usual TRex layout for STL traffic profiles.

---

## How TRex runs this profile

This script is an **STL profile module**.
It exposes the TRex entry point:

```python
def get_streams(direction=0, **kwargs):
```

and returns a list of `STLStream` objects.
TRex loads the file, calls `get_streams`, parses the tunables passed after `-t`, and then starts the generated streams.

So the workflow is:

1. start the **TRex server** in one terminal,
2. open the **TRex console** in another terminal,
3. load and start the profile from the TRex console using `start -f ...`.

---

## Running the profile

### 1. Start the TRex server

In the TRex server directory, start TRex in interactive mode:

```bash
sudo ./t-rex-64 -i
```

This starts the TRex server process and prepares it to accept commands from the TRex client console.

### 2. Open the TRex console

In another terminal, open the TRex console:

```bash
./trex-console
```

From this point on, commands such as `start -f ...` are entered **inside the TRex console**, not in the normal Linux shell.

### 3. Start the profile from the TRex console

Example:

```text
start -f stl/test_burst_dist.py -p 0 -d 15 -t --pktsize 128 --bursty --pps 0.5 --burst_us 10000 --gap_ms 100 --bursts 2 --dist 2
```

---

## TRex command explanation

The example command is entered inside the **TRex console**:

```text
start -f stl/test_burst_dist.py -p 0 -d 15 -t --pktsize 128 --bursty --pps 0.5 --burst_us 10000 --gap_ms 100 --bursts 2 --dist 2
```

### `start`
Start traffic from a profile in the TRex console.

### `-f stl/test_burst_dist.py`
Load this Python STL profile file.

### `-p 0`
Use TRex port `0` as the transmit port.

### `-d 15`
Run traffic for 15 seconds.

### `-t`
Pass the remaining arguments as **profile tunables** to the script.
These are parsed by the script's `argparse` logic inside `get_streams()`.

---

## Profile tunables

### `--pktsize 128`
Set the total packet size to 128 bytes.
The script pads the packet payload so the final packet length matches this value.

### `--pps 0.5`
Set the packet rate **inside each burst**.

In this profile, `--pps` is interpreted as **MPPS**.
So:

- `--pps 1.0` means **1 Mpps**,
- `--pps 0.5` means **0.5 Mpps**,
- `--pps 2.0` means **2 Mpps**.

This value controls the inter-packet spacing inside a burst.
For example:

- `--pps 1.0` gives **1 packet every 1 us**,
- `--pps 0.5` gives **1 packet every 2 us**,
- `--pps 2.0` gives **1 packet every 0.5 us**.

### `--burst_us 10000`
Set the burst duration to 10000 microseconds.

The number of packets in each burst is determined by both:

- `--pps`, and
- `--burst_us`.

For example, with:

- `--pps 0.5`
- `--burst_us 10000`

this gives about:

```text
5000 packets per burst
```

because the packet spacing is 2 us and the burst lasts 10000 us.

### `--gap_ms 100`
Set the idle gap between bursts to 100 ms.

### `--bursts 2`
Send 2 bursts.

So with:

- `--pps 0.5`
- `--burst_us 10000`
- `--bursts 2`

this profile generates about:

```text
10000 packets total
```

which is why TRex may report that it is attaching 10000 streams.

### `--start_delay_us`
Optional start delay before the first packet is sent.
Default is `0`.

### `--distribute 2` or `--dist 2`
Distribute traffic across 2 shards / destination-IP values.

The script accepts both:

```text
--distribute
```

and:

```text
--dist
```

as the same option.

With `--dist 2`, the profile alternates packet destinations across:

- shard 0 -> `0.0.0.0`
- shard 1 -> `0.0.0.1`

In general, with `--distribute N`, shard `k` uses destination IP equal to integer value `k`.

---

## Compatibility argument

### `--bursty`
Accepted for compatibility.
The profile is already burst-based, so this flag does not change the scheduling logic.

---

## Packet format

Each generated packet is built as:

```text
Ether / IP / UDP / GTPULayer / IP / UDP / Raw
```

The outer headers are:

- Ethernet
- outer IPv4
- outer UDP with source/destination port 2152
- custom `GTPULayer`

The inner headers are:

- inner IPv4
- inner UDP with source port 2153 and destination port 2152

The remainder of the packet is padded with raw bytes to reach the requested total size.

---

## Distribution behavior

The script pre-builds one packet template per shard.
For shard `k`, the destination IP is generated by:

```python
shard_to_ip(k)
```

This guarantees:

```text
int(IPv4Address(shard_to_ip(k))) == k
```

So if downstream logic uses something like:

```text
dstAddr % N
```

Then shard `k` maps cleanly to interface `k` for `0 <= k < N`.

---

## Notes for users

When changing the traffic pattern, users usually only need to edit:

- `--pktsize`
- `--pps`
- `--burst_us`
- `--gap_ms`
- `--bursts`
- `--start_delay_us`
- `--distribute` or `--dist`

or the packet-building code itself if a different encapsulation format is needed.

If the profile is used together with **EnergyTracer**, the one-packet-per-stream design is useful because the resulting burst timing can be introduced more explicitly and inspected more clearly in the trace timeline.
