"""IBKR native wire pacing, with fresh query evidence and ordered writes.

Integration with ib_insync Client.sendMsg (ib_insync 0.9.86), whose native
queue/window/event protocol is retained; the per-channel and order scheduling
is implemented here. Upstream source: https://github.com/erdewit/ib_insync

BSD 2-Clause License
Copyright (c) 2019, Ewald de Wit
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
from __future__ import annotations
from collections import deque
from v6lite.config import LiteConfig

QUERY_OPCODE_CHANNELS = {"61": "positions", "5": "openOrders", "16": "openOrders", "7": "executions", "99": "completedOrders"}
ORDER_OPCODES = {"3", "4", "8", "58"}  # placeOrder, cancelOrder, reqIds, reqGlobalCancel
MAX_MESSAGES = 10
MESSAGE_WINDOW_SECONDS = 1.0


def query_intervals(cfg: LiteConfig) -> dict[str, float]:
    return {"openOrders": cfg.open_orders_min_interval_seconds,
            "completedOrders": cfg.open_orders_min_interval_seconds,
            "positions": cfg.positions_min_interval_seconds,
            "executions": cfg.executions_min_interval_seconds}


class IbkrMessagePacer:
    def __init__(self, cfg: LiteConfig, on_throttle, on_reset=None):
        self.intervals = query_intervals(cfg)
        self.on_throttle = on_throttle
        self.on_reset = on_reset
        self.sent_times = deque()
        self.last_sent: dict[str, float] = {}
        self.client = None
        self.timer = None
        self.draining = False
        self.order_guard = None  # scoped, synchronous TP pre-wire expiry check
        self.generation = 0

    @staticmethod
    def opcode(message):
        return message.split("\0", 1)[0]

    def install(self, client):
        if client is None:
            return
        client.MaxRequests = MAX_MESSAGES
        client.RequestsInterval = MESSAGE_WINDOW_SECONDS
        if self.client is client:
            return
        if not all(hasattr(client, name) for name in ("sendMsg", "_msgQ", "_timeQ", "conn", "reset", "isConnected")):
            return  # offline stand-ins still use the adapter's real-wait guard
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self.client is not None:
            self.invalidate(self.client)
        self.client = client
        self.generation += 1
        original_reset = client.reset
        def reset():
            self.invalidate(client)
            result = original_reset()
            if self.on_reset is not None and self.client is client:
                self.on_reset()
            return result
        client.reset = reset
        client.sendMsg = lambda message: self.send_message(client, message)

    def invalidate(self, client):
        if self.client is client:
            self.generation += 1
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
        # No queued order is allowed to survive a socket generation. Pacing
        # clocks are only rate history, never broker or End evidence.
        client._msgQ.clear()
        client._timeQ.clear()

    def resume(self, client, generation):
        if self.client is client and self.generation == generation:
            self.send_message(client, None)

    def send_message(self, client, message):
        # Dynamic lookup keeps ib_insync's loop ownership. This callback never
        # sleeps, invents an End, or reads/returns a cached broker snapshot.
        from ib_insync import client as native_client
        loop = native_client.getLoop()
        generation = self.generation
        if self.client is not client or not client.isConnected():
            return
        messages, times = client._msgQ, client._timeQ

        def live():
            return (generation == self.generation and self.client is client and
                    client._msgQ is messages and client._timeQ is times and client.isConnected())

        def refresh_window():
            now = loop.time()
            while self.sent_times and now - self.sent_times[0] >= MESSAGE_WINDOW_SECONDS:
                self.sent_times.popleft()
            times.clear()
            times.extend(self.sent_times)
            return now

        if message:
            if self.opcode(message) in ORDER_OPCODES:
                last_order = max((i for i, queued in enumerate(messages)
                                  if self.opcode(queued) in ORDER_OPCODES), default=-1)
                messages.insert(last_order + 1, message)
            else:
                messages.append(message)
            channel = QUERY_OPCODE_CHANNELS.get(self.opcode(message))
            now = refresh_window()
            if channel and (now < self.last_sent.get(channel, float("-inf")) + self.intervals[channel] or
                            len(self.sent_times) >= MAX_MESSAGES):
                self.on_throttle(channel)
        # A synchronous notice handler can reset/reconnect or drain work itself.
        if not live() or self.draining:
            return
        self.draining = True
        try:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            while messages:
                if not live():
                    return
                # Notice handlers and connection writes may consume real time.
                # Every packet must recheck the actual clock/window/readiness.
                now = refresh_window()
                if len(self.sent_times) >= MAX_MESSAGES:
                    break
                selected = None
                for index, queued in enumerate(messages):
                    channel = QUERY_OPCODE_CHANNELS.get(self.opcode(queued))
                    if channel is None or now >= self.last_sent.get(channel, float("-inf")) + self.intervals[channel]:
                        selected = index
                        break
                if selected is None:
                    break
                queued = messages[selected]
                del messages[selected]
                if self.order_guard is not None and self.opcode(queued) == "3":
                    self.order_guard(queued)
                client.conn.sendMsg(client._prefix(queued.encode()))
                # Write completion is a conservative physical-wire boundary:
                # callback/socket cost cannot move this packet into the past.
                sent_at = loop.time()
                self.sent_times.append(sent_at)
                channel = QUERY_OPCODE_CHANNELS.get(self.opcode(queued))
                if channel:
                    self.last_sent[channel] = sent_at
                if not live():
                    return  # keep global rate history, never touch the dead queue
                times.append(sent_at)
                if client._logger.isEnabledFor(10):
                    client._logger.debug(">>> %s", queued[:-1].replace("\0", ","))
            if messages:
                if not client._isThrottling:
                    client._isThrottling = True
                    client.throttleStart.emit()
                    client._logger.debug("Started to throttle requests")
                if not live():
                    return
                now = refresh_window()
                ready = min((self.last_sent.get(QUERY_OPCODE_CHANNELS[self.opcode(queued)], float("-inf")) +
                             self.intervals[QUERY_OPCODE_CHANNELS[self.opcode(queued)]]
                             if self.opcode(queued) in QUERY_OPCODE_CHANNELS else now)
                            for queued in messages)
                capacity = self.sent_times[0] + MESSAGE_WINDOW_SECONDS if len(self.sent_times) >= MAX_MESSAGES else now
                self.timer = loop.call_at(max(now, ready, capacity), self.resume, client, generation)
            elif client._isThrottling:
                client._isThrottling = False
                client.throttleEnd.emit()
                if not live():
                    return
                client._logger.debug("Stopped to throttle requests")
        finally:
            self.draining = False
            # A reset inside conn.sendMsg can enqueue new-generation work
            # reentrantly. Resume only that live queue; the old deque is dead.
            current = self.client
            if (self.timer is None and current is not None and current.isConnected() and
                    current._msgQ):
                self.timer = loop.call_at(loop.time(), self.resume, current, self.generation)

    def queued_seconds(self):
        messages = getattr(self.client, "_msgQ", ())
        # Conservative budget: each queued query can consume its full channel
        # interval, in addition to the common rolling message budget.
        return len(messages) / MAX_MESSAGES + sum(
            self.intervals.get(QUERY_OPCODE_CHANNELS.get(self.opcode(message)), 0)
            for message in messages)
