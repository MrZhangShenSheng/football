"""freq-band 比分选法（2026-08-27 重设计）：联赛频率模板+球队实力平移+形状带+q排序。开发者 sszhang
铁律（docs/2026-08-27-freq-band-design.html）：分布形状来自真实赛果频率（不造分布）；
赔率只做形状带门槛、不做排序；DC 不参与比分选择；数据缺失零破坏降级（纯联赛模板）。"""
import glob, json, math, re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from band_calibration import DIVS, SEASONS, fetch_rows
from common import ROOT
from score_ev import map_league

RECENT_WINDOW = 10          # 球队近况窗口（场）
FORM_MIN_MATCHES = 5        # 近况最少场数：不足 → 不平移（纯联赛模板降级）
SURVIVE_Q = 0.01            # 生存阈值：平移后 q < 1% 的比分视为噪声
LAMBDA_CLAMP = (0.2, 4.5)   # λ 合理域（防小样本狂胜拉爆）
SIGMA_FLOOR = 0.5           # 核带宽下限（防极端单点分布 σ→0）
LEAGUE_STORE = ("japan", "korea", "sweden", "saudi")   # 本地赛果库联赛（与 score_ev 口径一致）
BAND_DEFAULT = (10.0, 28.0)   # CRS 形状带（桂林-梅州合并带，与 boldplay 现状一致；boldplay.band_ok 是 had 方向带不可复用）
DIVERGENCE_FLAG_PP = 5        # |q − 市场隐含| 超此值(百分点)触发分歧旗（spec D12）
MARKET_MARGIN_DIV = 1.13      # 体彩三向去水除数近似
LOW_CONF_N = 100              # 模板样本低置信线（spec D9：不砍池只标旗）


def _norm(name: str) -> str:
    """队名宽松匹配键（与 boldplay._dc_params 同口径）：小写去连字符去空格。"""
    return str(name).lower().replace("-", "").replace(" ", "")


def league_base_rates(blob: Counter) -> tuple:
    """频率 Counter → (主场场均进球, 客场场均进球)：Σh·c/n 与 Σa·c/n。"""
    n = blob.get("__n", 0)
    if not n:
        return 0.0, 0.0
    gh = sum(int(s.split(":")[0]) * c for s, c in blob.items() if s != "__n")
    ga = sum(int(s.split(":")[1]) * c for s, c in blob.items() if s != "__n")
    return gh / n, ga / n


def global_pool(freq_table: dict) -> Counter:
    """全联赛汇总 Counter（欧冠/巴甲等无映射联赛的模板池）。"""
    g = Counter()
    for blob in freq_table.values():
        g.update(blob)
    return g


def build_team_form(fetch_rows_fn=fetch_rows,
                    league_glob="data/02-results/league/*_matches.json",
                    aliases: dict | None = None) -> dict:
    """fd CSV + 本地联赛库 → {norm队名: [(进球, 失球), ...]}（源内按时间序，取尾即最近）。
    fd 队名(HomeTeam/AwayTeam) 与本地 tid 统一 norm 化做键；fd 行经 aliases.fd 对照双写 tid 键
    （tid≠fd 名的队：bayern vs Bayern Munich——体彩中文→zh_map→tid 才能命中 fd 数据）。
    测试以 fetch_rows_fn/league_glob/aliases 注入隔离网络。"""
    if aliases is None:
        from common import load_aliases
        aliases = load_aliases()
    fd_to_tid = {}
    for tid, srcs in aliases.items():
        if isinstance(srcs, dict) and srcs.get("fd"):
            fd_to_tid[_norm(srcs["fd"])] = _norm(tid)

    def add(name: str, row: tuple):
        key = _norm(name)
        form[key].append(row)
        tid_key = fd_to_tid.get(key)
        if tid_key and tid_key != key:
            form[tid_key].append(row)     # fd 名 → tid 双键（同一行两键各存一份）

    form = defaultdict(list)
    for season in SEASONS:
        for div in DIVS:
            for r in fetch_rows_fn(season, div):
                try:
                    hg, ag = int(r["FTHG"]), int(r["FTAG"])
                    add(r["HomeTeam"], (hg, ag))
                    add(r["AwayTeam"], (ag, hg))
                except (KeyError, ValueError, TypeError):
                    continue
    for path in glob.glob(league_glob):
        key = path.replace("\\", "/").split("/")[-1].replace("_matches.json", "")
        if key not in LEAGUE_STORE:
            continue
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data.get("matches", []) if isinstance(data, dict) else (data or [])
        for m in rows:
            try:
                form[_norm(m["home"])].append((int(m["hg"]), int(m["ag"])))
                form[_norm(m["away"])].append((int(m["ag"]), int(m["hg"])))
            except (KeyError, ValueError, TypeError):
                continue
    return dict(form)


def team_strength(form: dict, norm_name: str):
    """norm队名 → 近 RECENT_WINDOW 场 (场均进, 场均失)；不足 FORM_MIN_MATCHES → None。"""
    rows = form.get(norm_name) or []
    if len(rows) < FORM_MIN_MATCHES:
        return None
    recent = rows[-RECENT_WINDOW:]
    return (sum(r[0] for r in recent) / len(recent),
            sum(r[1] for r in recent) / len(recent))


def lambdas(base: tuple, home_str, away_str) -> tuple:
    """乘法进球模型（Maher/Dixon-Coles 只取 λ 计算，不用 Poisson 造分布）：
    λ_h = 主队场均进 × 客队场均失 / 模板主场场均；λ_a 对称。clamp 防极端。"""
    if not home_str or not away_str or base[0] <= 0 or base[1] <= 0:
        return None
    lh = home_str[0] * away_str[1] / base[0]
    la = away_str[0] * home_str[1] / base[1]
    return (min(max(lh, LAMBDA_CLAMP[0]), LAMBDA_CLAMP[1]),
            min(max(la, LAMBDA_CLAMP[0]), LAMBDA_CLAMP[1]))


def shifted_q(blob: Counter, lam=None) -> dict:
    """联赛频率 Counter → {比分: q}。lam=(λh,λa) 时按 (t=h+a, d=h−a) 双轴高斯核平移：
    靠近本场目标的比分按距离比例抬升、靠近基准的回落——形状仍来自真实频率
    （c=0 的格子恒 0：真实数据从未出现的比分永不被选）。lam=None → 纯频率归一化。"""
    n = blob.get("__n", 0)
    if not n:
        return {}
    counts = {s: c for s, c in blob.items() if s != "__n"}
    coords = {}
    for s in counts:
        h, a = (int(x) for x in s.split(":"))
        coords[s] = (h + a, h - a)
    t_bar = sum(coords[s][0] * c for s, c in counts.items()) / n
    d_bar = sum(coords[s][1] * c for s, c in counts.items()) / n
    sig_t2 = max(sum((coords[s][0] - t_bar) ** 2 * c for s, c in counts.items()) / n, SIGMA_FLOOR ** 2)
    sig_d2 = max(sum((coords[s][1] - d_bar) ** 2 * c for s, c in counts.items()) / n, SIGMA_FLOOR ** 2)
    if lam is None:
        return {s: c / n for s, c in counts.items()}
    T, D = lam[0] + lam[1], lam[0] - lam[1]
    w = {}
    for s, c in counts.items():
        t, d = coords[s]
        g = (((t - t_bar) ** 2 - (t - T) ** 2) / (2 * sig_t2)
             + ((d - d_bar) ** 2 - (d - D) ** 2) / (2 * sig_d2))
        w[s] = c * math.exp(g)
    z = sum(w.values())
    return {s: v / z for s, v in w.items()} if z else {}


def freq_legs(odds_day: dict, freq_table: dict, form: dict, zh: dict, band: tuple) -> list:
    """三步选法（docs/2026-08-27-freq-band-design.html 图2）：
    ①生存阈：模板真实频率 q_pure≥1%（平移不救真实出现不足的比分——数据说话）
    ②赔率∈形状带 ③带内平移后 q 最高（每场 1 腿），跨场按 q 降序。
    模板池：联赛映射命中用联赛频率，否则全局池；近况缺失自动降级纯模板。"""
    lo, hi = band
    pool = global_pool(freq_table)
    legs = []
    for m in odds_day.get("matches", []):
        lg = map_league(m.get("league", ""))
        blob = freq_table.get(lg) if lg else None
        blob = blob if (blob and blob.get("__n", 0)) else pool
        if not blob.get("__n", 0):
            continue                                   # 全局池也空 → 无数据不选
        base = league_base_rates(blob)
        lam = lambdas(base,
                      team_strength(form, _norm(zh.get(m.get("home", ""), ""))),
                      team_strength(form, _norm(zh.get(m.get("away", ""), ""))))
        q_pure = shifted_q(blob, None)
        q_rank = shifted_q(blob, lam)
        best = None                                    # (q, leg)
        for s, o in (m.get("crs") or {}).items():
            if ":" not in s:                           # 胜其他/平其他/负其他
                continue
            o = float(o)
            if not (lo <= o <= hi):
                continue
            if q_pure.get(s, 0.0) < SURVIVE_Q:
                continue
            q = q_rank.get(s, 0.0)
            if best is None or q > best[0]:
                best = (q, {"matchNumStr": m["matchNumStr"],
                            "match": f'{m.get("home")}-{m.get("away")}',
                            "score": s, "odds": o, "q": round(q, 4),
                            "shifted": lam is not None})
        if best:
            legs.append(best[1])
    legs.sort(key=lambda l: -l["q"])
    return legs


def ttg_agg(q_map: dict) -> dict:
    """比分分布 → 体彩总进球8档 {s0..s7}（s7=7+并桶；'胜其他'等长尾并入s7保守）。
    纯分桶不改形状（spec §4.2）。开发者 sszhang"""
    out = {f"s{k}": 0.0 for k in range(8)}
    for s, q in q_map.items():
        if ":" not in s:
            out["s7"] += q
            continue
        h, a = (int(x) for x in s.split(":"))
        out[f"s{min(h + a, 7)}"] += q
    return out


def _selftest_ttg():
    q = {"1:0": 0.20, "0:1": 0.15, "2:2": 0.10, "3:3": 0.05, "0:0": 0.30, "2:0": 0.12, "4:3": 0.08}
    # 归一化fixture Σ=1.00
    out = ttg_agg(q)
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert abs(out["s0"] - 0.30) < 1e-9          # 0:0
    assert abs(out["s1"] - 0.35) < 1e-9          # 1:0 + 0:1
    assert abs(out["s4"] - 0.10) < 1e-9          # 2:2
    assert abs(out["s6"] - 0.05) < 1e-9          # 3:3
    assert abs(out["s7"] - 0.08) < 1e-9          # 4:3 → 7球并入s7(=7+)
    assert abs(out["s2"] - 0.12) < 1e-9          # 2:0
    print("[selftest] ttg_agg OK")


HAFU_KEYS = [f"{x}{y}" for x in "hda" for y in "hda"]
ALPHA_CACHE = ROOT / "engine" / "cache" / "hafu_alpha.json"


def _half_ft_rows(results_dir=None):
    """02-results → [(半场三向idx, 全场三向idx)]（half+result 齐全的场次）。
    去重口径(评审fix)：排除 -rN 过程快照；主文件跨日/文件内按(主队,客队)同场去重(取首现)。
    开发者 sszhang"""
    base = Path(results_dir) if results_dir else ROOT / "data" / "02-results"
    rows, seen = [], set()
    for p in sorted(base.glob("2*.json")):
        if re.search(r"-r\d+\.json$", p.name):    # -rN 过程快照不算观测
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in data.get("matches") or []:
            sc, hf = m.get("result"), m.get("half")
            if not (sc and hf and ":" in str(hf)):
                continue
            try:
                hg, ag = (int(x) for x in str(sc).replace("-", ":").split(":"))
                hh_, ha_ = (int(x) for x in str(hf).split(":"))
            except ValueError:
                continue
            name = str(m.get("match") or "")
            if " vs " in name:                    # 同场去重键=(主队,客队)；无名场次不参与去重
                key = tuple(x.strip() for x in name.split(" vs ", 1))
                if key in seen:
                    continue
                seen.add(key)
            tri = lambda a, b: 0 if a > b else (1 if a == b else 2)
            rows.append((tri(hh_, ha_), tri(hg, ag)))
    return rows


def half_three_way(results_dir=None) -> dict:
    """本地半场三向经验频率 {"h","d","a"}（α 与 HAFU 聚合共用数据源）。开发者 sszhang"""
    rows = _half_ft_rows(results_dir)
    n = len(rows)
    if not n:
        return {"h": 1/3, "d": 1/3, "a": 1/3}     # 无数据→均匀退化(低置信)
    c = [0, 0, 0]
    for x, _ in rows:
        c[x] += 1
    return {"h": c[0]/n, "d": c[1]/n, "a": c[2]/n}


def hafu_alpha(results_dir=None) -> dict:
    """观测9键频率 / [P_half×P_FT]全局 → 纠偏系数α（spec §4.2 v1.1·D8）。
    全局口径混合联赛(选择偏差诚实标注于JSON note)；样本0的键α=1.0不纠。
    缓存 engine/cache/hafu_alpha.json 仅 results_dir=None 时读写（自定义目录现算不污染全局缓存）。
    开发者 sszhang"""
    rows = _half_ft_rows(results_dir)
    n = len(rows)
    use_cache = results_dir is None
    if use_cache and ALPHA_CACHE.exists():
        try:
            cached = json.loads(ALPHA_CACHE.read_text(encoding="utf-8"))
            if cached.get("n") == n:
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    obs = {k: 0 for k in HAFU_KEYS}
    ph = [0, 0, 0]; pf = [0, 0, 0]
    for x, y in rows:
        obs[HAFU_KEYS[x * 3 + y]] += 1
        ph[x] += 1; pf[y] += 1
    tri = "hda"
    alpha = {}
    for k in HAFU_KEYS:
        x, y = tri.index(k[0]), tri.index(k[1])
        base = (ph[x] / n) * (pf[y] / n)
        alpha[k] = round((obs[k] / n) / base, 4) if base > 0 and obs[k] >= 5 else 1.0
    out = {"alpha": alpha, "n": n, "ranAt": str(date.today()),
           "note": "全局口径·混合联赛·含选场偏差; obs<5键不纠(α=1)"}
    if use_cache:
        ALPHA_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ALPHA_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def hafu_agg(q_map: dict, p_half: dict, alpha: dict) -> dict:
    """半场三向(经验) × 本场FT三向(q_map聚合) × α → 九键归一（spec §4.2）。
    注意: half_share.json 为 s/ρ_half 标量结构非九键，v1 用本地经验 P_half（侦察确认）。开发者 sszhang"""
    ph_ft = sum(q for s, q in q_map.items() if ":" in s and int(s.split(":")[0]) > int(s.split(":")[1]))
    pa_ft = sum(q for s, q in q_map.items() if ":" in s and int(s.split(":")[0]) < int(s.split(":")[1]))
    p_ft = {"h": ph_ft, "d": max(1 - ph_ft - pa_ft, 0.0), "a": pa_ft}
    raw = {f"{x}{y}": p_half[x] * p_ft[y] * alpha.get(f"{x}{y}", 1.0) for x in "hda" for y in "hda"}
    z = sum(raw.values())
    return {k: v / z for k, v in raw.items()} if z else {k: 1/9 for k in HAFU_KEYS}


def _selftest_hafu():
    res = hafu_alpha()                       # 真实02-results数据
    assert res["n"] >= 50, f"半场样本不足: {res['n']}"   # 去重真实口径≈61·评审裁定诚实下限
    assert res["alpha"]["dd"] >= 1.0, f"α(dd)必须≥1(方向性·spec D8): {res['alpha']['dd']}"
    p_half = half_three_way()
    assert abs(sum(p_half.values()) - 1.0) < 1e-6
    # 构造q_map: 纯主胜分布 → FT=(1,0,0) → 九键只剩 xh
    qm = {"2:0": 0.5, "3:1": 0.5}
    alpha1 = {k: 1.0 for k in ("hh","hd","ha","dh","dd","da","ah","ad","aa")}
    out = hafu_agg(qm, p_half, alpha1)
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert abs(out["hh"] + out["dh"] + out["ah"] - 1.0) < 1e-9   # FT全主胜→xh三键占满
    # 纠偏生效: α(hh)=2 时 hh 翻倍(归一化前)
    alpha2 = dict(alpha1); alpha2["hh"] = 2.0
    out2 = hafu_agg(qm, p_half, alpha2)
    assert out2["hh"] > out["hh"] * 1.5      # 归一化稀释后仍显著抬升
    print(f"[selftest] hafu_alpha + hafu_agg OK (n={res['n']} 去重口径)")


def pools_card(m: dict, q_map: dict, form: dict, zh: dict, freq_table: dict) -> dict:
    """单场三池玩法卡（spec §4.3 v1.2）：CRS带内top1 + TTG top2 + HAFU top2，
    EV=q×体彩赔率−1；双行推荐（保底=相近带q最高/翻身=相近带赔率最高）；
    CRS分歧旗（EV全场最高且|q−市场隐含|>5pp → 标divergent+翻身带剔除·I1裁定）；
    低置信旗（模板缺失/样本<LOW_CONF_N，不砍池推荐走结构兜底=抽水低池优先）。开发者 sszhang"""
    lg = map_league(m.get("league", ""))
    blob = freq_table.get(lg) if lg else None
    n_tpl = (blob or {}).get("__n", 0) if blob else 0
    flags = []
    if n_tpl < LOW_CONF_N:                         # 映射缺失(None)/空模板 → n_tpl=0 同样低置信
        flags.append("low_conf")
    # 联赛模板优先, 缺则全局池(freq_legs 同款降级); q_map 已含平移
    q_eff = q_map if q_map else shifted_q(global_pool(freq_table) if freq_table else Counter(), None)
    p_half, alpha_doc = half_three_way(), hafu_alpha()
    alpha = alpha_doc.get("alpha", {})
    ttg_q = ttg_agg(q_eff)
    hafu_q = hafu_agg(q_eff, p_half, alpha)
    code, match = m.get("code"), f'{m.get("home")} vs {m.get("away")}'
    cands = []
    crs_top = None
    for s, o in (m.get("crs") or {}).items():
        o = float(o)
        if ":" in s and BAND_DEFAULT[0] <= o <= BAND_DEFAULT[1]:      # 形状带门槛(铁律8)
            c = {"code": code, "match": match, "pool": "crs", "pick": s,
                 "q": round(q_eff.get(s, 0.0), 4), "odds": o}
            if crs_top is None or c["q"] > crs_top["q"]:
                crs_top = c
    if crs_top and crs_top["q"] > 0:
        crs_top["ev"] = round(crs_top["q"] * crs_top["odds"] - 1, 4)
        cands.append(crs_top)
    ttg_c = sorted(({"code": code, "match": match, "pool": "ttg", "pick": k,
                     "q": round(v, 4), "odds": float(o)}
                    for k, o in (m.get("ttg") or {}).items() for v in [ttg_q.get(k, 0.0)] if v > 0),
                   key=lambda c: -c["q"])[:2]
    hafu_c = sorted(({"code": code, "match": match, "pool": "hafu", "pick": k,
                      "q": round(v, 4), "odds": float(o)}
                     for k, o in (m.get("hafu") or {}).items() for v in [hafu_q.get(k, 0.0)] if v > 0),
                    key=lambda c: -c["q"])[:2]
    for c in ttg_c + hafu_c:
        c["ev"] = round(c["q"] * c["odds"] - 1, 4)
    cands.extend(ttg_c + hafu_c)
    if not cands:
        return {"code": code, "match": match,
                "candidates": [], "flags": flags + ["no_candidates"]}
    # CRS 分歧旗（spec D12·终审I1裁定落地）: CRS 候选 EV 为全场最高 且 |q−市场隐含|>阈值
    # → 标 divergent=True（留候选展示并降级末位）；翻身推荐带剔除（模型傲慢不进决策链）
    if cands[0]["pool"] == "crs" and cands[0]["ev"] >= max(c["ev"] for c in cands):
        implied = 1.0 / (cands[0]["odds"] * MARKET_MARGIN_DIV)
        if (cands[0]["q"] - implied) * 100 > DIVERGENCE_FLAG_PP:
            flags.append("divergence")
            cands[0]["divergent"] = True
            cands.append(cands.pop(0))            # 降级到末位=第二推荐（展示口径）
    # 双行推荐: EV 相近带(差<0.1·演示档)内, 保底取q最高/翻身取赔率最高；
    # 翻身带只取非分歧候选（band=max EV among 非分歧），全分歧→最高q非CRS候选或None
    # （本场不出翻身腿）；保底带不受分歧旗影响（保底视角命中优先）
    evs = [c["ev"] for c in cands]
    band = [c for c in cands if c["ev"] >= max(evs) - 0.1]
    rec_base = max(band, key=lambda c: c["q"])
    nd = [c for c in cands if not c.get("divergent")]
    if nd:
        rec_upset = max((c for c in nd if c["ev"] >= max(c["ev"] for c in nd) - 0.1),
                        key=lambda c: c["odds"])
    else:
        non_crs = [c for c in cands if c["pool"] != "crs"]
        rec_upset = max(non_crs, key=lambda c: c["q"]) if non_crs else None
    if "low_conf" in flags:                        # 结构兜底: 抽水低池优先(spec D9)
        low_vig = [c for c in band if c["pool"] in ("ttg", "hafu")]
        if low_vig:
            rec_base = max(low_vig, key=lambda c: c["q"])
    return {"code": code, "match": match,
            "candidates": cands, "rec_base": rec_base, "rec_upset": rec_upset, "flags": flags}


def _selftest_pools():
    # ── Scenario A（双行推荐）：两候选 EV 相近 → 保底取 q 最高 / 翻身取赔率最高 ──
    # 手算: q_map={"0:2":.09,"1:1":.10,"2:1":.12} → ttg_q: s2=.19, s3=.12（0:2/1:1 均总2球）
    #   crs 1:1  q=.100 @11.5 → EV=1.150−1=.150；隐含 1/(11.5×1.13)=7.7% → Δ2.3pp<5pp 无分歧
    #   ttg s3   q=.120 @9.5  → EV=1.140−1=.140；EV差.010<0.1 → 双候选同带
    mA = {"code": "周六001", "league": "德乙", "home": "A队", "away": "B队",
          "crs": {"1:1": 11.5}, "ttg": {"s3": 9.5}}
    ftA = {"germany-2-bundesliga": Counter({"1:1": 40, "2:2": 20, "__n": 200})}  # n=200≥100
    cardA = pools_card(mA, {"0:2": 0.09, "1:1": 0.10, "2:1": 0.12}, {}, {}, ftA)
    assert cardA["code"] == "周六001" and cardA["match"] == "A队 vs B队"
    assert cardA["rec_base"]["pool"] == "ttg" and cardA["rec_base"]["pick"] == "s3"      # 保底=q最高(.12>.10)
    assert cardA["rec_upset"]["pool"] == "crs" and cardA["rec_upset"]["pick"] == "1:1"  # 翻身=赔率最高(11.5>9.5)
    assert "divergence" not in cardA["flags"] and "low_conf" not in cardA["flags"]
    assert all(abs(c["ev"] - (c["q"] * c["odds"] - 1)) < 1e-4 for c in cardA["candidates"])  # 1e-4=4位小数舍入容差

    # ── Scenario B（分歧旗·I1裁定）：CRS 0:2 q=11.5%@24 vs 隐含 1/(24×1.13)≈3.7% → Δ7.8pp>5pp ──
    # 手算: crs 0:2 EV=.115×24−1=1.76 全场最高；1:1@7.5 低于带下限10出局；ttg s2=.195@3.75 EV=−.2688
    #   divergent 标记留候选展示；翻身带=非分歧候选（hafu dd）→ CRS 不进翻身推荐；
    #   保底带仍含 CRS（保底视角命中优先）
    mB = {"code": "周日004", "league": "德乙", "home": "圣保利", "away": "凯泽",
          "crs": {"0:2": 24.0, "1:1": 7.5}, "ttg": {"s2": 3.75, "s3": 3.55},
          "hafu": {"dd": 6.25, "hh": 2.75}}
    cardB = pools_card(mB, {"0:2": 0.115, "1:1": 0.08}, {}, {}, ftA)
    assert "divergence" in cardB["flags"]
    assert cardB["candidates"][0]["pool"] != "crs"                     # 降级后 CRS 不居首
    assert any(c.get("divergent") for c in cardB["candidates"])        # 被旗候选标 divergent 留展示
    assert cardB["rec_upset"]["pool"] != "crs"                         # 分歧CRS不进翻身推荐(I1裁定)
    assert cardB["rec_upset"]["pool"] in ("ttg", "hafu")               # 翻身=非分歧带(ttg/hafu候选)
    assert cardB["rec_base"]["pool"] == "hafu" and cardB["rec_base"]["pick"] == "dd"  # 保底=带内q最高(dd .433>CRS .115)·rec_base计算不看divergent(I1裁定:分歧只排翻身)
    assert all(c["code"] == "周日004" and c["match"] == "圣保利 vs 凯泽"
               for c in cardB["candidates"])                           # 候选自带 code/match（T4 契约）
    assert all(abs(c["ev"] - (c["q"] * c["odds"] - 1)) < 1e-4 for c in cardB["candidates"])

    # ── Scenario D（全分歧退路）：唯一候选=分歧CRS → rec_upset=None（本场不出翻身腿）──
    mD = {"code": "周六020", "league": "德乙", "home": "C队", "away": "D队", "crs": {"0:2": 24.0}}
    cardD = pools_card(mD, {"0:2": 0.115}, {}, {}, ftA)
    assert "divergence" in cardD["flags"] and cardD["rec_upset"] is None
    assert cardD["rec_base"]["pool"] == "crs"                          # 保底视角仍可落CRS

    # ── Scenario C（低置信兜底）：联赛无映射(芬超)+空模板 → low_conf 旗 + 低抽水池兜底 ──
    # q_map 空 → q_eff 空 → crs/ttg 全 q=0 出局；hafu_agg 空 q_map → FT 三向全平 → d 列非零
    mC = {"code": "周一002", "league": "芬超", "home": "赫尔火花", "away": "TPS",
          "crs": {"0:2": 20.0}, "ttg": {"s3": 3.55}, "hafu": {"dd": 6.25}}
    cardC = pools_card(mC, {}, {}, {}, {})
    assert "low_conf" in cardC["flags"]
    assert cardC["rec_base"]["pool"] == "hafu"                         # 结构兜底=抽水低池优先(spec D9)
    print("[selftest] pools_card OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest_ttg()
        _selftest_hafu()
        _selftest_pools()
