"""Live synthetic feed: new packets, submissions, payer acks, denials and HIM rescans."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, timedelta
from typing import TYPE_CHECKING

from .. import ops
from ..domain.models import CaseStatus
from ..domain.policies import PAYERS
from ..workflows.intake import run_intake
from .generator import Generator

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger("lotus.feed")


class Feed:
    def __init__(self, c: "Container", seed: int = 7):
        self.c = c
        existing = [int(x.split("-")[1]) for x in c.store.ids("cases")]
        self.gen = Generator(seed=seed + len(existing), start_case=max(existing, default=1000) + 1,
                             demo_today=ops.demo_today())
        self.rng = random.Random(seed)
        self.paused = False
        self.speed = 1
        self._task: asyncio.Task | None = None

    def state(self) -> dict:
        return {"paused": self.paused, "speed": self.speed, "running": self._task is not None}

    # ------------------------------------------------------------------ seed
    async def seed(self) -> None:
        """A deterministic starting book of work covering every status."""
        if self.c.store.count("cases"):
            return
        c, g = self.c, self.gen
        today = ops.demo_today()

        async def new(defects=None, scenario=None, service=None) -> str:
            spec = g.packet(defects=defects, scenario=scenario, service=service)
            c.repo.save(spec.case)
            await run_intake(c, spec.case.id)
            return spec.case.id

        async def to_ack(cid: str) -> None:
            await ops.submit(c, cid)
            await ops.payer_ack(c, cid)

        # Denied cases, deadlines ~10 and ~16 days out.
        for scenario, code, days_left in (("sepsis", "CO-50", 10), ("appendectomy", "CO-16", 16),
                                          ("chest_pain_obs", "CO-50", 31)):
            payer_days = 45  # worst case window; real window comes from the payer below
            cid = await new([], scenario, service=today - timedelta(days=payer_days + 20))
            await to_ack(cid)
            case = c.repo.get(cid)
            notice = today - timedelta(days=PAYERS[case.payer]["appeal_days"] - days_left)
            if notice <= date.fromisoformat(case.service_date):
                notice = date.fromisoformat(case.service_date) + timedelta(days=12)
            await ops.receive_denial(c, g, cid, code, notice)
        # One appeal already decided.
        cid = await new([], "hip_fracture")
        await to_ack(cid)
        case = c.repo.get(cid)
        await ops.receive_denial(c, g, cid, "CO-11", today - timedelta(days=5))
        from ..workflows.appeal import resume_appeal
        await resume_appeal(c, cid, {"action": "prepare", "actor": "J. Rivera"})
        if c.repo.get(cid).appeal.letter.gate_passed:
            await resume_appeal(c, cid, {"action": "approve", "actor": "J. Rivera"})
        # Integrity exceptions of every kind, plus clean flow.
        for defects in (["missing_doc"], ["identity"], ["unreadable"], ["skew"], ["low_ocr"],
                        ["missing_doc", "identity"], [], []):
            await new(defects)
        for _ in range(2):
            cid = await new([])
            await to_ack(cid)
            await ops.archive(c, cid)
        cid = await new([])
        await ops.submit(c, cid)
        ops.deadline_sweep(c)

    # ------------------------------------------------------------------ live
    async def step(self) -> None:
        c, rng = self.c, self.rng
        cases = c.repo.all()
        by = lambda s: [x for x in cases if x.status == s]  # noqa: E731
        choices: list[tuple[float, str]] = [(3.0, "new")]
        if by(CaseStatus.READY):
            choices.append((2.5, "submit"))
        if by(CaseStatus.SENT):
            choices.append((2.0, "ack"))
        if by(CaseStatus.ACKNOWLEDGED):
            choices += [(1.0, "deny"), (1.5, "archive")]
        if by(CaseStatus.AWAITING_RESCAN):
            choices.append((1.5, "rescan"))
        action = rng.choices([a for _, a in choices], weights=[w for w, _ in choices])[0]
        if action == "new":
            spec = self.gen.packet()
            c.repo.save(spec.case)
            await run_intake(c, spec.case.id)
        elif action == "submit":
            await ops.submit(c, rng.choice(by(CaseStatus.READY)).id)
        elif action == "ack":
            await ops.payer_ack(c, rng.choice(by(CaseStatus.SENT)).id)
        elif action == "deny":
            await ops.receive_denial(c, self.gen, rng.choice(by(CaseStatus.ACKNOWLEDGED)).id)
        elif action == "archive":
            await ops.archive(c, rng.choice(by(CaseStatus.ACKNOWLEDGED)).id)
        elif action == "rescan":
            await ops.deliver_rescan(c, rng.choice(by(CaseStatus.AWAITING_RESCAN)).id)
        ops.deadline_sweep(c)

    async def run(self) -> None:
        interval = self.c.settings.simulator_interval_s
        while True:
            await asyncio.sleep(interval / self.speed)
            if self.paused:
                continue
            try:
                await self.step()
            except Exception:  # keep the feed alive; errors are visible in logs and traces
                log.exception("feed step failed")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
