"""half_engine 两阶段(HAFU)推演出口测试：与现状派生版数学同构(段A对照可比性基础) /
剧本树自洽 / dc_only 与 fused 口径透传. 开发者 sszhang"""
import dc_predict
import half_engine


def test_isomorphic_to_hafu_approx():
    # 同λ同k/rho_half → 9键与现状派生版一致(同截断同归一)——两条对照线数学同构,
    # 段A对照时差异仅来自λ口径(fused vs 裸DC)与P1-w比分态修正, 此为可比性地基
    for lh, la, k, rho in [(1.5, 1.0, 0.45, 0.0), (2.1, 0.7, 0.44, 0.09),
                           (0.8, 0.9, 0.46, -0.05), (1.0, 1.0, 0.5, 0.12)]:
        ours = half_engine.hafu_tree(lh, la, k, rho)["hafu"]
        ref = dc_predict.hafu_approx(lh, la, k, rho)
        for key in half_engine.HAFU_KEYS:
            assert abs(ours[key] - ref[key]) < 1e-9, (lh, la, key, ours[key], ref[key])


def test_tree_self_consistency():
    out = half_engine.hafu_tree(1.6, 1.1, 0.44, 0.05)
    hafu, tree = out["hafu"], out["tree"]
    assert abs(sum(hafu.values()) - 1.0) < 1e-9
    assert abs(sum(out["half"]) - 1.0) < 1e-5          # half=分支p(round 6位)
    for i in "hda":
        assert abs(sum(tree[i]["ft"]) - tree[i]["p"]) < 1e-4
        assert tree[i]["top_score"]["p"] <= tree[i]["p"] + 1e-9
        assert tree[i]["top_ft"] == "hda"[max(range(3), key=lambda j: tree[i]["ft"][j])]
        # 分支p = 该半场三向的9键行和
        assert abs(sum(hafu[i + j] for j in "hda") - tree[i]["p"]) < 1e-5


def test_hafu_dc_only_passthrough():
    out = half_engine.hafu("spain-laliga", 1.5, 1.0, -0.05, mkt=None)
    assert out["source"] == "dc_only"
    assert 0.30 <= out["k"] <= 0.60                    # half_share sClamp 域
    assert abs(sum(out["hafu"].values()) - 1.0) < 1e-9


def test_hafu_fused_lambda_source():
    mkt = {"lh": 2.0, "la": 0.5, "rho": 0.0, "fit_err": 0.001}
    out = half_engine.hafu("spain-laliga", 1.5, 1.0, -0.05, mkt=mkt)
    assert out["source"] == "fused"
    assert out["lambda"]["lh"] > 0
    # 强主融合λ → 半场主胜分支概率应显著高于客胜分支
    assert out["tree"]["h"]["p"] > out["tree"]["a"]["p"]
