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


def check_shared_legs(bets: list) -> list:
    """共用腿跨注复用告警（spec §二）：T033 三注共用米堡2:1，表面3注实为1个失效点。"""
    from collections import Counter
    cnt = Counter(leg for b in bets for leg in (b.get("legs") or []))
    return [f"⚠腿「{leg}」被 {n} 注复用：表面 {len(bets)} 注实为伪分散，"
            f"该腿一断则 {n} 注同灭(T033 教训)"
            for leg, n in cnt.items() if n > 1]
