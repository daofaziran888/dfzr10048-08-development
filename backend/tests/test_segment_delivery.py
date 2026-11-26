"""分段交付验收测试：局部补发、迟到签收抵扣、重送去重、凭据复查、跨收件方隔离。

场景主线：新版首尾改字、中段不变；发送人已领新版且发到第 100 行（未送出第 101 行），
旧包中段签收迟到；回到发送页确认已发与抵扣区间；不再发中段时可核对停止位置与凭据，
仍继续发时接收方只得到一次有效签收；换一个未签旧包的收件方应收到完整三段。
"""
from __future__ import annotations

import pytest

from app.pipeline.segment_delivery import (
    SegmentDeliveryLedger,
    default_pack_lines,
    derive_revision_lines,
    segment_checksum,
    split_segments,
)
from app.services.delivery_receipt import DeliveryReceiptService
from app.services.segment_delivery import SegmentDeliveryService
from app.services.subtitle_pack import SubtitlePackService
from app.store import store


def make_packs(suffix: str) -> tuple[dict, dict]:
    """造一对旧/新字幕包：300 行三段，新版首尾改字、中段不变。"""
    packs = SubtitlePackService()
    old, _ = packs.create_pack(f"SBPK-{suffix}-OLD", f"MEDI-{suffix}", "v1", 300, 7200, "SRT")
    new, _ = packs.create_revision_pack(
        f"SBPK-{suffix}-OLD", f"SBPK-{suffix}-NEW", "v2", edit_head=True, edit_tail=True)
    assert old is not None and new is not None
    return old, new


def make_receipt(suffix: str, name: str, pack_code: str, recipient: str) -> dict:
    receipts = DeliveryReceiptService()
    entry, missing = receipts.create_entry({
        "回执编号": f"DLRC-{suffix}-{name}",
        "关联字幕包": pack_code,
        "接收方标识": recipient,
        "交付行数": "0",
        "交付格式": "SRT",
        "交付结论": "-",
        "重交次数": "0",
        "交付时间": "-",
    })
    assert not missing
    return entry


def receipt_of(pack_code: str, recipient: str) -> dict:
    return next(
        row for row in store.rows("delivery_receipt")
        if row["关联字幕包"] == pack_code and row["接收方标识"] == recipient
    )


class TestSegmentHelpers:
    def test_split_segments_even(self):
        assert split_segments(300) == [(1, 100), (101, 200), (201, 300)]

    def test_split_segments_remainder(self):
        assert split_segments(128) == [(1, 43), (44, 86), (87, 128)]

    def test_split_segments_empty(self):
        assert split_segments(0) == []

    def test_revision_lines_head_tail_only(self):
        old_lines = default_pack_lines("MEDI-X", 300)
        new_lines = derive_revision_lines(old_lines, edit_head=True, edit_tail=True)
        # 首尾段校验和改变，中段校验和不变
        assert segment_checksum(old_lines, 1, 100) != segment_checksum(new_lines, 1, 100)
        assert segment_checksum(old_lines, 101, 200) == segment_checksum(new_lines, 101, 200)
        assert segment_checksum(old_lines, 201, 300) != segment_checksum(new_lines, 201, 300)

    def test_ledger_dedup(self):
        ledger = SegmentDeliveryLedger()
        record, dup1 = ledger.register("平台A", 1, 100, "cksum", "SGV-1", "P1", "R1")
        assert not dup1
        again, dup2 = ledger.register("平台A", 1, 100, "cksum", "SGV-2", "P2", "R2")
        assert dup2 and again["voucher"] == "SGV-1"
        # 内容不同则是新签收（改过的行必须送达）
        _, dup3 = ledger.register("平台A", 1, 100, "cksum-new", "SGV-3", "P2", "R2")
        assert not dup3
        # 收件方不同则互不影响
        _, dup4 = ledger.register("平台B", 1, 100, "cksum", "SGV-4", "P1", "R1")
        assert not dup4


class TestPartialRedeliverFlow:
    """主场景：不再发中段的分支。"""

    def test_full_scenario_skip_middle(self):
        suffix = "S1"
        recipient = "内容分发平台A"
        old_pack, new_pack = make_packs(suffix)
        old_code = old_pack["字幕包编号"]
        new_code = new_pack["字幕包编号"]
        old_receipt = make_receipt(suffix, "OLD", old_code, recipient)
        new_receipt = make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()
        receipts = DeliveryReceiptService()

        # 1. 局部补发入口：为新版生成待发段，三段均待生成（旧包尚未有签收）
        entry, message = receipts.run_action(int(new_receipt["id"]), "局部补发")
        assert entry is not None, message
        summary = service.plan_summary(new_code, recipient)
        assert [seg["status"] for seg in summary["segments"]] == ["待生成"] * 3
        assert summary["pending_ranges"] == [[1, 100], [101, 200], [201, 300]]

        # 2. 发送人领取新版首段并发出 1-100 行（尚未送出第 101 行）
        ok, message, _ = service.claim_segment(new_code, recipient, 1)
        assert ok, message
        ok, message, summary = service.record_progress(new_code, recipient, 100)
        assert ok, message
        assert summary["stop_line"] == 100
        ok, message, summary = service.sign_segment(new_code, recipient, 1)
        assert ok, message
        first_voucher = summary["segments"][0]["voucher"]
        assert first_voucher.startswith("SGV-")
        assert receipt_of(new_code, recipient)["交付行数"] == "100"
        assert receipt_of(new_code, recipient)["status"] == "部分签收"

        # 3. 旧包中段签收迟到：旧包先生成计划，再送达并签收中段
        service.generate_plan(old_code, recipient)
        ok, message, _ = service.claim_segment(old_code, recipient, 2)
        assert ok, message
        ok, message, _ = service.record_progress(old_code, recipient, 200)
        assert ok, message
        ok, message, _ = service.sign_segment(old_code, recipient, 2)
        assert ok, message

        # 4. 回到发送页：新版中段被旧签收抵扣，已发/抵扣/待发区间清晰
        summary = service.plan_summary(new_code, recipient)
        statuses = [seg["status"] for seg in summary["segments"]]
        assert statuses == ["已签收", "已抵扣", "待生成"]
        assert summary["sent_ranges"] == [[1, 100]]
        assert summary["credited_ranges"] == [[101, 200]]
        assert summary["pending_ranges"] == [[201, 300]]
        assert summary["stop_line"] == 100
        credited = summary["segments"][1]
        assert credited["credit_source"] == old_receipt["回执编号"]
        assert credited["voucher"] != "-"
        # 抵扣计入交付行数但不重复：100（首段签收）+ 100（中段抵扣）= 200
        assert receipt_of(new_code, recipient)["交付行数"] == "200"

        # 5. 这次不再发中段：直接领取尾段发到第 300 行并签收
        ok, message, _ = service.claim_segment(new_code, recipient, 3)
        assert ok, message
        ok, message, summary = service.record_progress(new_code, recipient, 300)
        assert ok, message
        assert summary["stop_line"] == 300
        ok, message, summary = service.sign_segment(new_code, recipient, 3)
        assert ok, message
        assert [seg["status"] for seg in summary["segments"]] == ["已签收", "已抵扣", "已签收"]
        # 实际停止位置与回执可核对：整包 300 行办结
        final = service.plan_summary(new_code, recipient)
        assert final["stop_line"] == 300
        assert receipt_of(new_code, recipient)["交付行数"] == "300"
        assert receipt_of(new_code, recipient)["status"] == "已签收"

        # 6. 刷新后按原凭据复查：换一个服务实例（模拟刷新）结果一致
        refreshed = SegmentDeliveryService()
        records = refreshed.lookup_voucher(first_voucher)
        assert len(records) == 1
        assert records[0]["关联字幕包"] == new_code
        assert records[0]["片段区间"] == "1-100"
        assert records[0]["status"] == "已签收"
        # 中段抵扣段的凭据来自旧包签收，两个包都能凭同一凭据复查到
        credit_voucher = credited["voucher"]
        credit_records = refreshed.lookup_voucher(credit_voucher)
        assert {r["关联字幕包"] for r in credit_records} == {old_code, new_code}

        # 7. 字幕包办结
        packs = SubtitlePackService()
        pack_entry = next(r for r in store.rows("subtitle_pack") if r["字幕包编号"] == new_code)
        entry, message = packs.run_action(int(pack_entry["id"]), "办结")
        assert entry is not None, message
        assert entry["status"] == "已办结"

    def test_resend_middle_not_double_counted(self):
        """仍继续发中段的分支：接收方只得到一次有效签收。"""
        suffix = "S2"
        recipient = "内容分发平台A"
        old_pack, new_pack = make_packs(suffix)
        old_code = old_pack["字幕包编号"]
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "OLD", old_code, recipient)
        make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()

        service.generate_plan(new_code, recipient)
        service.claim_segment(new_code, recipient, 1)
        service.record_progress(new_code, recipient, 100)
        service.sign_segment(new_code, recipient, 1)

        # 旧包中段签收迟到 → 新版中段抵扣
        service.generate_plan(old_code, recipient)
        service.claim_segment(old_code, recipient, 2)
        service.record_progress(old_code, recipient, 200)
        service.sign_segment(old_code, recipient, 2)
        summary = service.plan_summary(new_code, recipient)
        assert summary["segments"][1]["status"] == "已抵扣"
        original_voucher = summary["segments"][1]["voucher"]
        assert receipt_of(new_code, recipient)["交付行数"] == "200"

        # 仍继续发中段：已抵扣段可直接登记送达，签收被判重复、不重复计行
        ok, message, _ = service.record_progress(new_code, recipient, 200)
        assert ok, message
        ok, message, summary = service.sign_segment(new_code, recipient, 2)
        assert ok, message
        assert "重复签收" in message
        middle = summary["segments"][1]
        assert middle["status"] == "已签收"
        assert middle["duplicate"] == "是"
        assert middle["voucher"] == original_voucher  # 凭据仍是首次那份
        assert receipt_of(new_code, recipient)["交付行数"] == "200"  # 不重复计行

        # 尾段照常送达签收，整包只计 300 行一次
        service.claim_segment(new_code, recipient, 3)
        service.record_progress(new_code, recipient, 300)
        service.sign_segment(new_code, recipient, 3)
        assert receipt_of(new_code, recipient)["交付行数"] == "300"
        assert receipt_of(new_code, recipient)["status"] == "已签收"
        # 凭据复查：中段内容只有一份有效签收
        records = SegmentDeliveryService().lookup_voucher(original_voucher)
        assert {r["关联字幕包"] for r in records} == {old_code, new_code}
        assert all(r["签收凭据"] == original_voucher for r in records)

    def test_new_recipient_gets_full_three_segments(self):
        """未签旧包的收件方：旧签收不跨收件方生效，应收到完整三段。"""
        suffix = "S3"
        old_pack, new_pack = make_packs(suffix)
        old_code = old_pack["字幕包编号"]
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "OLD", old_code, "内容分发平台A")
        make_receipt(suffix, "NEW-A", new_code, "内容分发平台A")
        make_receipt(suffix, "NEW-B", new_code, "视频归档系统")
        service = SegmentDeliveryService()

        # 平台A 旧包三段全部签收
        service.generate_plan(old_code, "内容分发平台A")
        for seg_no, upto in ((1, 100), (2, 200), (3, 300)):
            service.claim_segment(old_code, "内容分发平台A", seg_no)
            service.record_progress(old_code, "内容分发平台A", upto)
            service.sign_segment(old_code, "内容分发平台A", seg_no)

        # 平台A 新版：中段抵扣，首尾必须送达（改过的行必须送达）
        summary = service.generate_plan(new_code, "内容分发平台A")
        assert [seg["status"] for seg in summary["segments"]] == ["待生成", "已抵扣", "待生成"]

        # 视频归档系统未签旧包：新版三段全部待生成，无抵扣
        summary_b = service.generate_plan(new_code, "视频归档系统")
        assert [seg["status"] for seg in summary_b["segments"]] == ["待生成"] * 3
        assert summary_b["credited_ranges"] == []
        assert summary_b["pending_ranges"] == [[1, 100], [101, 200], [201, 300]]

        # 视频归档系统完整收到三段
        for seg_no, upto in ((1, 100), (2, 200), (3, 300)):
            ok, message, _ = service.claim_segment(new_code, "视频归档系统", seg_no)
            assert ok, message
            ok, message, _ = service.record_progress(new_code, "视频归档系统", upto)
            assert ok, message
            ok, message, _ = service.sign_segment(new_code, "视频归档系统", seg_no)
            assert ok, message
        receipt_b = receipt_of(new_code, "视频归档系统")
        assert receipt_b["交付行数"] == "300"
        assert receipt_b["status"] == "已签收"
        summary_b = service.plan_summary(new_code, "视频归档系统")
        assert [seg["status"] for seg in summary_b["segments"]] == ["已签收"] * 3

    def test_changed_lines_must_be_delivered(self):
        """旧签收只在内容相同的区间生效：首尾改过的行不产生抵扣。"""
        suffix = "S4"
        recipient = "内容分发平台A"
        old_pack, new_pack = make_packs(suffix)
        old_code = old_pack["字幕包编号"]
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "OLD", old_code, recipient)
        make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()

        # 旧包三段全部签收后，新版计划只抵扣中段
        service.generate_plan(old_code, recipient)
        for seg_no, upto in ((1, 100), (2, 200), (3, 300)):
            service.claim_segment(old_code, recipient, seg_no)
            service.record_progress(old_code, recipient, upto)
            service.sign_segment(old_code, recipient, seg_no)
        summary = service.generate_plan(new_code, recipient)
        statuses = [seg["status"] for seg in summary["segments"]]
        assert statuses == ["待生成", "已抵扣", "待生成"]
        assert summary["pending_ranges"] == [[1, 100], [201, 300]]


class TestPackPageActions:
    def test_claim_remaining_and_close(self):
        suffix = "S5"
        recipient = "内容分发平台A"
        _, new_pack = make_packs(suffix)
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()
        packs = SubtitlePackService()
        pack_entry = next(r for r in store.rows("subtitle_pack") if r["字幕包编号"] == new_code)

        # 字幕包页领取剩余文件：生成计划并领取全部待生成段
        entry, message = packs.run_action(int(pack_entry["id"]), "领取剩余文件")
        assert entry is not None, message
        assert "领取剩余 3 段" in message
        summary = service.plan_summary(new_code, recipient)
        assert [seg["status"] for seg in summary["segments"]] == ["已领取"] * 3

        # 剩余字幕文件是实际的待送行
        remaining = service.remaining_lines(new_code, recipient)
        assert remaining is not None
        filename, text, count = remaining
        assert count == 300
        assert "第001行" in text and "第300行" in text

        # 未签收前不能办结
        entry, message = packs.run_action(int(pack_entry["id"]), "办结")
        assert entry is None
        assert "不能办结" in message

        # 全部送达签收后办结
        for seg_no, upto in ((1, 100), (2, 200), (3, 300)):
            service.record_progress(new_code, recipient, upto)
            service.sign_segment(new_code, recipient, seg_no)
        entry, message = packs.run_action(int(pack_entry["id"]), "办结")
        assert entry is not None, message
        assert entry["status"] == "已办结"

    def test_claim_remaining_respects_credit(self):
        """领取剩余文件只领取真正待发的片段，抵扣段不在剩余文件里。"""
        suffix = "S6"
        recipient = "内容分发平台A"
        old_pack, new_pack = make_packs(suffix)
        old_code = old_pack["字幕包编号"]
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "OLD", old_code, recipient)
        make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()
        packs = SubtitlePackService()

        # 旧包中段签收 → 新版中段抵扣
        service.generate_plan(old_code, recipient)
        service.claim_segment(old_code, recipient, 2)
        service.record_progress(old_code, recipient, 200)
        service.sign_segment(old_code, recipient, 2)

        pack_entry = next(r for r in store.rows("subtitle_pack") if r["字幕包编号"] == new_code)
        entry, message = packs.run_action(int(pack_entry["id"]), "领取剩余文件")
        assert entry is not None, message
        assert "领取剩余 2 段" in message
        summary = service.plan_summary(new_code, recipient)
        assert [seg["status"] for seg in summary["segments"]] == ["已领取", "已抵扣", "已领取"]
        _, text, count = service.remaining_lines(new_code, recipient)
        assert count == 200
        assert "第101行" not in text


class TestSegmentGuards:
    def test_sign_requires_claim_and_full_send(self):
        suffix = "S7"
        recipient = "内容分发平台A"
        _, new_pack = make_packs(suffix)
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()
        service.generate_plan(new_code, recipient)

        ok, message, _ = service.sign_segment(new_code, recipient, 1)
        assert not ok and "未领取" in message
        service.claim_segment(new_code, recipient, 1)
        ok, message, _ = service.sign_segment(new_code, recipient, 1)
        assert not ok and "尚未送完" in message
        ok, message, _ = service.record_progress(new_code, recipient, 50)
        assert ok, message
        ok, message, _ = service.sign_segment(new_code, recipient, 1)
        assert not ok and "尚未送完" in message

    def test_progress_requires_claim(self):
        suffix = "S8"
        recipient = "内容分发平台A"
        _, new_pack = make_packs(suffix)
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()
        service.generate_plan(new_code, recipient)
        ok, message, _ = service.record_progress(new_code, recipient, 100)
        assert not ok and "未领取" in message
        ok, message, _ = service.record_progress(new_code, recipient, 999)
        assert not ok and "超出" in message

    def test_plan_unknown_pack(self):
        service = SegmentDeliveryService()
        assert service.generate_plan("SBPK-NONE", "某人") is None

    def test_generic_action_entry(self):
        """通用动作入口（/api/segment_delivery/{id}/actions）也能驱动领取与签收。"""
        suffix = "S9"
        recipient = "内容分发平台A"
        _, new_pack = make_packs(suffix)
        new_code = new_pack["字幕包编号"]
        make_receipt(suffix, "NEW", new_code, recipient)
        service = SegmentDeliveryService()
        service.generate_plan(new_code, recipient)
        row = next(
            r for r in store.rows("segment_delivery")
            if r["关联字幕包"] == new_code and r["接收方标识"] == recipient
        )
        entry, message = service.run_action(int(row["id"]), "领取片段")
        assert entry is not None and entry["status"] == "已领取"
        service.record_progress(new_code, recipient, 100)
        entry, message = service.run_action(int(row["id"]), "确认签收")
        assert entry is not None and entry["status"] == "已签收"
        assert entry["签收凭据"].startswith("SGV-")


class TestSeedScenarioData:
    """种子数据自洽：旧包首段已签收，新版包首尾改字、中段不变。"""

    def test_seed_packs_revision_relation(self):
        packs = store.rows("subtitle_pack")
        old = next(r for r in packs if r["字幕包编号"] == "SBPK-0004")
        new = next(r for r in packs if r["字幕包编号"] == "SBPK-0005")
        old_lines, new_lines = old["_lines"], new["_lines"]
        assert len(old_lines) == len(new_lines) == 300
        assert segment_checksum(old_lines, 101, 200) == segment_checksum(new_lines, 101, 200)
        assert segment_checksum(old_lines, 1, 100) != segment_checksum(new_lines, 1, 100)
        assert segment_checksum(old_lines, 201, 300) != segment_checksum(new_lines, 201, 300)

    def test_seed_signed_segment_matches_ledger(self):
        service = SegmentDeliveryService()
        seed_row = store.rows("segment_delivery")[0]
        assert seed_row["内容校验和"]
        # 旧包首段签收已在台账内：同内容区间再次生成计划时会被抵扣
        summary = service.generate_plan("SBPK-0004", "内容分发平台A")
        assert summary["segments"][0]["status"] == "已签收"
        # 新版首段改了字，不能被旧首段签收抵扣
        summary_new = service.generate_plan("SBPK-0005", "内容分发平台A")
        assert summary_new["segments"][0]["status"] == "待生成"
