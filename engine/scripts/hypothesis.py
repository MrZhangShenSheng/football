"""假设层（spec §二 2026-09-16 拍板）：撤销自动闸门后唯一的拦截机制。

三段式 assumption / checks[] / verdict。verdict=refuted 或 pending 的腿不得出票。
拦的是没做功课，不是拦胆量——反例：米堡2:1「主场场均3球所以能打2:1」，
H2H 四场米堡主场两次仅进1球、米尔沃尔防守型，数据明确反对。开发者 sszhang"""

VERDICTS = ("survived", "refuted", "pending")


def make_hypothesis(assumption: str, checks: list | None = None,
                    verdict: str = "pending") -> dict:
    return {"assumption": assumption, "checks": list(checks or []), "verdict": verdict}


def validate_hypothesis(h: dict) -> list:
    """返回问题清单，空列表=通过。"""
    probs = []
    if not str(h.get("assumption") or "").strip():
        probs.append("assumption 为空：须写一句可证伪的判断")
    v = h.get("verdict")
    if v not in VERDICTS:
        probs.append(f"verdict 非法：{v!r}，仅允许 {VERDICTS}")
    checks = h.get("checks") or []
    h2h = [c for c in checks if c.get("kind") == "h2h"]
    if not h2h:
        probs.append("checks 缺 h2h 项：2026-09-15 残缺缓存曾致漏判(spec §二)")
    for c in h2h:
        if "seasonComplete" not in c:
            probs.append("h2h 项缺 seasonComplete：须记录数据源赛季完整度")
        if "gaps" not in c:
            probs.append("h2h 项缺 gaps：须记录缺口区间")
        if "matches" not in c:
            probs.append("h2h 项缺 matches：须记录交手场次数")
    return probs


def is_buyable(h: dict) -> bool:
    return h.get("verdict") == "survived" and not validate_hypothesis(h)


def filter_buyable(legs: list) -> tuple:
    """(可买腿, 被挡腿)。无 hypothesis 字段的腿视为 pending → 被挡。"""
    buyable, blocked = [], []
    for l in legs:
        h = l.get("hypothesis") or make_hypothesis("")
        (buyable if is_buyable(h) else blocked).append(l)
    return buyable, blocked


def dedup_same_match(legs: list) -> list:
    """铁律9 的组注层执行：一注内同场至多 1 腿，同场保留赔率最高者。

    候选池层不做这件事（2026-09-16 终审）：在候选池预筛会让每场只剩最高赔比分，
    而同场赔率最高恒等于庄家认为最不可能的比分——实测 19 场在售全变成
    0:5@1000/5:0@700，撤赔率上限想救的 4:0@50、3:0@60 反被挤掉。
    同场多腿分放**不同注**是合法分散，故只在成注时压同注冲突。"""
    best = {}
    for l in legs:
        code = l.get("matchNumStr")
        if code not in best or float(l.get("odds") or 0) > float(best[code].get("odds") or 0):
            best[code] = l
    return [l for l in legs if best.get(l.get("matchNumStr")) is l]


def sort_by_hypothesis(legs: list) -> list:
    """假设优先排序（大哥 2026-09-16 拍板选项 C）：survived → pending → refuted，
    段内按赔率降序。

    卡面顶部应是做过功课的腿，不是赔率最高的腿。纯赔率降序会把 0:5@1000 这类
    最荒谬比分永久顶在最前，等于用阅读顺序复活了被撤掉的过滤。"""
    rank = {"survived": 0, "pending": 1, "refuted": 2}
    return sorted(legs, key=lambda l: (
        rank.get((l.get("hypothesis") or {}).get("verdict"), 1),
        -float(l.get("odds") or 0)))


def check_shared_legs(bets: list) -> list:
    """共用腿跨注复用告警（spec §二）：T033 三注共用米堡2:1，表面3注实为1个失效点。"""
    from collections import Counter
    cnt = Counter(leg for b in bets for leg in (b.get("legs") or []))
    return [f"⚠腿「{leg}」被 {n} 注复用：表面 {len(bets)} 注实为伪分散，"
            f"该腿一断则 {n} 注同灭(T033 教训)"
            for leg, n in cnt.items() if n > 1]
