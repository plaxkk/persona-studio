"""User-reviewed milestones, weekly actions, daily log and two-week experiments."""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, timedelta, datetime

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from . import growth_plan
from .browser_reads import BrowserReads
from .jobs import JobError


class MilestoneEdit(BaseModel):
    target: int = Field(ge=1, le=10_000_000)
    deadline: date | None = None


class ActionEdit(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    problem: str = Field(default="", max_length=300)
    measure: str = Field(default="", max_length=300)
    effort: str = Field(default="", max_length=100)
    evidence: str = Field(default="", max_length=1000)


class PlanEdit(BaseModel):
    actions: list[ActionEdit] = Field(max_length=3)
    confirm: bool = False


class CheckinEdit(BaseModel):
    day: date
    kind: str = Field(pattern="^(original|reply|profile|other)$")
    note: str = Field(default="", max_length=1500)
    action_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    publication_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{32,36}$")
    minutes: int | None = Field(default=None, ge=0, le=1440)


class ExperimentCreate(BaseModel):
    hypothesis: str = Field(min_length=1, max_length=500)
    metric: str = Field(min_length=1, max_length=150)
    baseline: str = Field(default="", max_length=1000)


class ExperimentFinish(BaseModel):
    result: str = Field(default="", max_length=1500)
    decision: str = Field(default="", max_length=1000)


class RelationshipEdit(BaseModel):
    handle: str = Field(pattern=r"^[A-Za-z0-9_]{1,15}$")
    note: str = Field(min_length=1, max_length=1000)
    interaction_url: str = Field(default="", max_length=200)
    next_step: str = Field(default="", max_length=500)


def register(app, store, jobs, session):
    def account():
        value = growth_plan._account(store)
        if not value:
            raise HTTPException(422, "先在设置中连接 X 账号。")
        return value

    @app.get("/api/v1/growth/plan", dependencies=[Depends(session)])
    def plan():
        return growth_plan.overview(store)

    @app.post("/api/v1/growth/rewards/read", dependencies=[Depends(session)])
    def read_rewards():
        account()
        if not BrowserReads(store).connected((1, 4, 6)):
            raise HTTPException(409, "请将连接助手更新到 1.4.7 并连接本机。")
        try:
            return {"task_id": jobs.enqueue("growth_rewards", {}, dedupe="growth-rewards")}
        except JobError as exc:
            raise HTTPException(409, str(exc.code)) from exc

    @app.post("/api/v1/growth/followers/read", dependencies=[Depends(session)])
    def read_followers():
        account()
        if not BrowserReads(store).connected((1, 4, 6)):
            raise HTTPException(409, "请将连接助手更新到 1.4.7 并连接本机。")
        try:
            return {"task_id": jobs.enqueue("growth_followers", {}, dedupe="growth-followers")}
        except JobError as exc:
            raise HTTPException(409, str(exc.code)) from exc

    @app.put("/api/v1/growth/relationships/{handle}", dependencies=[Depends(session)])
    def edit_relationship(handle: str, body: RelationshipEdit):
        name = account()
        if handle.lower() != body.handle.lower():
            raise HTTPException(422, "账号链接与填写的账号不同。")
        if body.interaction_url and not re.fullmatch(
                r"https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]{1,30}", body.interaction_url):
            raise HTTPException(422, "请填写 X 帖子链接。")
        with store.db(True) as c:
            c.execute("INSERT INTO growth_relationship_notes VALUES(?,?,?,?,?,?) "
                      "ON CONFLICT(account,handle) DO UPDATE SET note=excluded.note,"
                      "interaction_url=excluded.interaction_url,next_step=excluded.next_step,"
                      "updated_at=excluded.updated_at",
                      (name, handle, body.note, body.interaction_url or None,
                       body.next_step, int(time.time())))
        return growth_plan.overview(store)

    @app.put("/api/v1/growth/milestones/{ident}", dependencies=[Depends(session)])
    def edit_milestone(ident: int, body: MilestoneEdit):
        stamp = int(time.time())
        with store.db(True) as c:
            row = c.execute("SELECT * FROM growth_milestones WHERE id=?", (ident,)).fetchone()
            if not row:
                raise HTTPException(404, "没有这个里程碑。")
            if row["completed_at"] is not None and body.target != row["target"]:
                raise HTTPException(409, "已完成的目标人数不能改写历史记录。")
            neighbors = c.execute("SELECT id,target FROM growth_milestones ORDER BY target").fetchall()
            index = next(i for i, item in enumerate(neighbors) if item["id"] == ident)
            if ((index and body.target <= neighbors[index - 1]["target"]) or
                    (index + 1 < len(neighbors) and body.target >= neighbors[index + 1]["target"])):
                raise HTTPException(422, "目标人数需要保持逐级递增。")
            other = c.execute("SELECT id FROM growth_milestones WHERE target=? AND id<>?",
                              (body.target, ident)).fetchone()
            if other:
                raise HTTPException(409, "已有同样的目标人数。")
            c.execute("UPDATE growth_milestones SET target=?,deadline=?,updated_at=? WHERE id=?",
                      (body.target, body.deadline.isoformat() if body.deadline else None, stamp, ident))
            store.event("growth", "已修改成长里程碑", {"id": ident, "target": body.target}, c)
        return growth_plan.overview(store)

    @app.post("/api/v1/growth/weeks/suggest", dependencies=[Depends(session)])
    def suggest():
        return growth_plan.suggest(store, account())

    @app.put("/api/v1/growth/weeks/{ident}", dependencies=[Depends(session)])
    def edit_week(ident: str, body: PlanEdit):
        name = account()
        stamp = int(time.time())
        with store.db(True) as c:
            row = c.execute("SELECT id,status FROM growth_week_plans WHERE id=? AND lower(account)=lower(?)",
                            (ident, name)).fetchone()
            if not row:
                raise HTTPException(404, "没有这周的计划。")
            if body.confirm and not body.actions:
                raise HTTPException(422, "至少确认一项行动。")
            # Keep IDs for unchanged slots so daily check-ins retain their link.
            old = c.execute("SELECT id,position FROM growth_actions WHERE plan_id=?", (ident,)).fetchall()
            old_by_position = {item["position"]: item["id"] for item in old}
            for position, action in enumerate(body.actions):
                if position in old_by_position:
                    c.execute("UPDATE growth_actions SET title=?,problem=?,measure=?,effort=?,evidence=?,updated_at=? WHERE id=?",
                              (action.title, action.problem, action.measure, action.effort,
                               action.evidence, stamp, old_by_position[position]))
                else:
                    c.execute("INSERT INTO growth_actions VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (uuid.uuid4().hex, ident, position, action.title, action.problem,
                               action.measure, action.effort, action.evidence, stamp, stamp))
            for position, action_id in old_by_position.items():
                if position >= len(body.actions):
                    c.execute("UPDATE growth_checkins SET action_id=NULL WHERE action_id=?", (action_id,))
                    c.execute("DELETE FROM growth_actions WHERE id=?", (action_id,))
            c.execute("UPDATE growth_week_plans SET status=?,updated_at=? WHERE id=?",
                      ("confirmed" if body.confirm else "suggested", stamp, ident))
            store.event("growth", "已确认每周成长计划" if body.confirm else "已修改每周成长计划",
                        {"plan_id": ident, "actions": len(body.actions)}, c)
        return growth_plan.overview(store)

    @app.post("/api/v1/growth/checkins", dependencies=[Depends(session)])
    def checkin(body: CheckinEdit):
        name = account()
        if body.day > growth_plan._today(store):
            raise HTTPException(422, "不能提前记录尚未发生的行动。")
        stamp = int(time.time())
        ident = uuid.uuid4().hex
        with store.db(True) as c:
            if body.action_id:
                linked = c.execute(
                    "SELECT p.week_start FROM growth_actions a JOIN growth_week_plans p ON p.id=a.plan_id "
                    "WHERE a.id=? AND lower(p.account)=lower(?) AND p.status='confirmed'",
                    (body.action_id, name)).fetchone()
                if not linked or not (date.fromisoformat(linked["week_start"]) <= body.day <
                                      date.fromisoformat(linked["week_start"]) + timedelta(days=7)):
                    raise HTTPException(422, "只能关联当天所在周的已确认行动。")
            if body.publication_id:
                published = c.execute("SELECT publication_kind,COALESCE(published_at,user_confirmed_at) AS occurred "
                                      "FROM draft_publications "
                                      "WHERE draft_id=? AND lower(author)=lower(?) AND user_confirmed=1",
                                      (body.publication_id, name)).fetchone()
                if not published:
                    raise HTTPException(422, "发布记录与当前账号不匹配。")
                if published["occurred"] and datetime.fromtimestamp(published["occurred"],
                        growth_plan.weekly.timezone(store)).date() != body.day:
                    raise HTTPException(422, "发布记录不属于打卡日期。")
                if c.execute("SELECT 1 FROM growth_checkins WHERE publication_id=?", (body.publication_id,)).fetchone():
                    raise HTTPException(409, "这条发布记录已经打卡。")
            c.execute("INSERT INTO growth_checkins VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (ident, name, body.day.isoformat(), body.action_id, body.kind,
                       body.note, body.publication_id, body.minutes, stamp, stamp))
        return growth_plan.overview(store)

    @app.post("/api/v1/growth/experiments", dependencies=[Depends(session)])
    def start_experiment(body: ExperimentCreate):
        name = account()
        stamp = int(time.time())
        with store.db(True) as c:
            if c.execute("SELECT 1 FROM experiments WHERE lower(account)=lower(?) AND status='active'",
                         (name,)).fetchone():
                raise HTTPException(409, "已有进行中的两周实验，先完成它。")
            ident = uuid.uuid4().hex
            c.execute("INSERT INTO experiments(id,account,hypothesis,metric,baseline_json,target_json,"
                      "started_at,planned_end_at,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (ident, name, body.hypothesis, body.metric,
                       json.dumps({"note": body.baseline}, ensure_ascii=False), "{}",
                       stamp, stamp + 14 * 86400, "active", stamp, stamp))
        return growth_plan.overview(store)

    @app.put("/api/v1/growth/experiments/{ident}", dependencies=[Depends(session)])
    def finish_experiment(ident: str, body: ExperimentFinish):
        name = account()
        stamp = int(time.time())
        with store.db(True) as c:
            row = c.execute("SELECT planned_end_at,status FROM experiments WHERE id=? AND lower(account)=lower(?)",
                            (ident, name)).fetchone()
            if not row:
                raise HTTPException(404, "没有这个实验。")
            if row["status"] != "active":
                raise HTTPException(409, "实验已经结束。")
            # Ending early is allowed, but does not turn a short sample into a conclusion.
            decision = body.decision if stamp >= row["planned_end_at"] else ""
            c.execute("UPDATE experiments SET ended_at=?,status='completed',result_json=?,decision=?,"
                      "updated=?,cloud_status='pending' WHERE id=?",
                      (stamp, json.dumps({"note": body.result, "early": stamp < row["planned_end_at"]},
                                         ensure_ascii=False), decision, stamp, ident))
        return growth_plan.overview(store)
