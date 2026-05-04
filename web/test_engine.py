"""
test_engine.py — Validation du moteur Girabase contre le VB6 original

Toutes les valeurs de référence sont calculées à la main depuis :
  - BRANCHE.cls  (getCVH, CalculParamBranche, TTP, KS, Tf)
  - TRAFIC.cls   (TraficGenant avec QSortantGenant, RéserveCapacité piétons)
  - GIRATOIRE.cls (CalculParamGiratoire, MajComplément — RU/LAU/KE/KI/LEU/LImax)
  - GirabaseMain.bas (constantes Tg/Te/Tf1 par milieu, coefLEU=1.2)

Lancer : pytest test_engine.py -v
"""
import math
import pytest
from engine import (
    GirabaseEngine, RoundaboutGeometry, ArmGeometry,
    TrafficPeriod, Milieu, _passes_entry,
    _Tg, _Te, _Tf1, COEF_LEU,
)

ATOL = 0.15   # tolérance absolue veh/h ou %


def make_engine(R, LA, Bf, milieu, arms_spec):
    geom = RoundaboutGeometry(R=R, LA=LA, Bf=Bf, milieu=milieu, n_arms=len(arms_spec))
    arms = []
    for spec in arms_spec:
        arms.append(ArmGeometry(
            name=spec.get("name", "?"),
            LE4m=spec.get("LE4m", 3.5),
            LE15m=spec.get("LE15m", 0.0),
            LI=spec.get("LI", 0.0),
            LS=spec.get("LS", 0.0),
            evasee=spec.get("evasee", False),
            has_ramp=spec.get("has_ramp", False),
            has_right_turn=spec.get("has_right_turn", False),
            pedestrians_per_hour=spec.get("ped", 0),
            exit_only=spec.get("exit_only", False),
        ))
    return GirabaseEngine(geom, arms)


def compute_one(engine, flows):
    result = engine.compute([TrafficPeriod(name="T", flows=flows)])
    return result.periods[0].arms


class TestGeomParams:
    def test_standard_pu(self):
        eng = make_engine(10, 8, 0, Milieu.PU, [{"name": "A", "LE4m": 3.5}])
        assert eng.RU  == pytest.approx(10.0, abs=1e-9)
        assert eng.LAU == pytest.approx(8.0,  abs=1e-9)
        assert eng.LEU == pytest.approx(8.0 / (1.2 * (1 + 1/(2*10))), rel=1e-6)
        assert eng.LImax == pytest.approx(4.55 * math.sqrt(14.0), rel=1e-6)
        assert eng.KI == pytest.approx(1.0, abs=1e-6)
        assert eng.KE == pytest.approx(1.0, abs=1e-9)

    def test_bf_nonzero(self):
        eng = make_engine(10, 8, 1, Milieu.PU, [{"name": "A", "LE4m": 3.5}])
        assert eng.RU  == pytest.approx(10.5, abs=1e-9)
        assert eng.LAU == pytest.approx(8.5,  abs=1e-9)

    def test_large_lau_ke_lt_1(self):
        eng = make_engine(10, 10, 0, Milieu.PU, [{"name": "A", "LE4m": 3.5}])
        expected_KE = 1.0 - (10.0/20.0)**2 * 2.0/10.0
        assert eng.KE == pytest.approx(expected_KE, rel=1e-6)
        assert eng.KI <= eng.KE

    def test_mini_gir_ru_lau(self):
        eng = make_engine(0, 5, 0, Milieu.PU, [{"name": "A", "LE4m": 3.0}])
        assert eng.RU  == pytest.approx(3.5, abs=1e-9)
        assert eng.LAU == pytest.approx(1.5, abs=1e-9)

    def test_mini_gir_forces_rc_constants(self):
        for m in [Milieu.PU, Milieu.CV]:
            eng = make_engine(0, 5, 0, m, [{"name": "A", "LE4m": 3.0}])
            assert eng.Tg  == pytest.approx(_Tg [Milieu.RC], rel=1e-9)
            assert eng.Te  == pytest.approx(_Te [Milieu.RC], rel=1e-9)
            assert eng.Tf1 == pytest.approx(_Tf1[Milieu.RC], rel=1e-9)

    def test_rext(self):
        eng = make_engine(10, 8, 1, Milieu.PU, [{"name": "A", "LE4m": 3.5}])
        assert eng.Rext == pytest.approx(19.0, abs=1e-9)


class TestArmParams:
    def _eng(self, arm_spec):
        return make_engine(10, 8, 0, Milieu.PU, [arm_spec])

    def test_le_simple(self):
        eng = self._eng({"name": "A", "LE4m": 4.0, "LE15m": 6.0, "evasee": False})
        assert eng._arm_LE[0] == pytest.approx(4.0, abs=1e-9)

    def test_le_evasee(self):
        eng = self._eng({"name": "A", "LE4m": 3.5, "LE15m": 5.5, "evasee": True})
        assert eng._arm_LE[0] == pytest.approx(4.5, abs=1e-9)

    def test_legirabase_clamp(self):
        eng = self._eng({"name": "A", "LE4m": 8.0})
        assert eng._arm_LEg[0] == pytest.approx(eng.LEU, rel=1e-6)

    def test_tf_no_ramp(self):
        eng = self._eng({"name": "A", "LE4m": 3.5, "has_ramp": False})
        assert eng._arm_Tf[0] == pytest.approx(_Tf1[Milieu.PU], rel=1e-9)

    def test_tf_ramp(self):
        eng = self._eng({"name": "A", "LE4m": 3.5, "has_ramp": True})
        assert eng._arm_Tf[0] == pytest.approx(_Tf1[Milieu.PU] * 1.35, rel=1e-6)

    def test_ttp_clamp(self):
        assert self._eng({"name":"A","LE4m":3.0})._arm_TTP[0] == pytest.approx(3.0, abs=1e-9)
        assert self._eng({"name":"A","LE4m":5.0})._arm_TTP[0] == pytest.approx(4.0, abs=1e-9)

    def test_ks_standard(self):
        eng = self._eng({"name": "A", "LE4m": 3.5, "LI": 0, "LS": 0})
        assert eng._arm_KS[0] == pytest.approx(10.0 / 18.0, rel=1e-4)

    def test_ks_with_li_ls(self):
        eng = self._eng({"name": "A", "LE4m": 3.5, "LI": 2.0, "LS": 6.0})
        LImax = _Tg[Milieu.PU] * math.sqrt(10.0 + 4.0)
        expected = max(0.0, 10.0/18.0 - (2.0 + 0.5)/LImax)
        assert eng._arm_KS[0] == pytest.approx(expected, rel=1e-4)

    def test_ks_non_negative(self):
        eng = self._eng({"name": "A", "LE4m": 3.5, "LI": 8.0, "LS": 10.0})
        assert eng._arm_KS[0] >= 0.0


class TestPassesEntry:
    def test_basic_passes(self):
        assert _passes_entry(2, 0, 1, 4) is True
        assert _passes_entry(3, 0, 1, 4) is True
        assert _passes_entry(3, 0, 2, 4) is True

    def test_does_not_pass(self):
        assert _passes_entry(1, 0, 2, 4) is False
        assert _passes_entry(0, 0, 1, 4) is False

    def test_all_4_arm_passing_before_est(self):
        assert _passes_entry(3, 1, 2, 4) is True
        assert _passes_entry(0, 1, 2, 4) is True
        assert _passes_entry(0, 1, 3, 4) is True
        assert _passes_entry(2, 1, 0, 4) is False
        assert _passes_entry(2, 1, 3, 4) is False

    def test_3_arms(self):
        assert _passes_entry(2, 0, 1, 3) is True
        assert _passes_entry(1, 0, 2, 3) is False


class TestCirculatingFlows:
    FLOWS = [
        [0,   150, 200, 200],
        [100,   0, 200, 150],
        [200, 200,   0, 330],
        [100, 100, 130,   0],
    ]

    @pytest.fixture
    def eng(self):
        arms = [{"name": n, "LE4m": 4.5} for n in ["Nord", "Est", "Sud", "Ouest"]]
        return make_engine(10, 8, 0, Milieu.PU, arms)

    def test_qg_with_correction(self, eng):
        KS = 10.0 / 18.0
        QT = [430, 530, 450, 500]
        QS = [400, 450, 530, 680]
        QG_expected = []
        for qt, qs in zip(QT, QS):
            qsg = KS * qs * (1 - qs / (qt + qs)) if qt + qs > 0 else 0
            QG_expected.append(qt + qsg)

        arms_result = compute_one(eng, self.FLOWS)
        for i, ar in enumerate(arms_result):
            assert ar.QG == pytest.approx(QG_expected[i], abs=ATOL), f"QG[{i}]"

    def test_qg_zero_traffic(self, eng):
        f = [[0]*4 for _ in range(4)]
        for ar in compute_one(eng, f):
            assert ar.QG == pytest.approx(0.0, abs=ATOL)


class TestCapacity:
    def test_cvh_formula(self):
        eng = make_engine(10, 8, 0, Milieu.PU, [{"name": "A", "LE4m": 4.5}])
        QG_val = 500.0
        Tf = _Tf1[Milieu.PU]
        Tg = _Tg [Milieu.PU]
        Te = _Te [Milieu.PU]
        LEg = min(eng.LEU, 4.5)
        exposant = -QG_val/3600.0 * (Tg - Tf/2.0)
        Ci = (3600.0/Tf) * math.exp(exposant)
        expected = Ci * (LEg/3.5)**Te
        cvh, _ = eng._cvh(0, QG_val)
        assert cvh == pytest.approx(expected, rel=1e-6)

    def test_reserve_capacity_formula(self):
        FLOWS = [[0,300],[200,0]]
        eng = make_engine(10, 8, 0, Milieu.PU, [{"name":"A","LE4m":3.5},{"name":"B","LE4m":3.5}])
        for ar in compute_one(eng, FLOWS):
            if ar.capacity_adj > 0:
                expected_rc = (1.0 - ar.QE/ar.capacity_adj)*100
                assert ar.reserve_pct == pytest.approx(expected_rc, abs=ATOL)


class TestPedestrians:
    def test_no_pedestrians(self):
        arms_spec = [{"name": n, "LE4m": 4.5, "ped": 0} for n in "ABCD"]
        eng = make_engine(10, 8, 0, Milieu.PU, arms_spec)
        FLOWS = [[0,100,100,100],[100,0,100,100],[100,100,0,100],[100,100,100,0]]
        for ar in compute_one(eng, FLOWS):
            assert ar.capacity_adj == pytest.approx(ar.capacity, abs=1e-6)

    def test_high_pedestrians_capped(self):
        arms_spec = [{"name": n, "LE4m": 4.5, "ped": 2500} for n in "ABCD"]
        eng = make_engine(10, 8, 0, Milieu.PU, arms_spec)
        FLOWS = [[0,100,100,100],[100,0,100,100],[100,100,0,100],[100,100,100,0]]
        for ar in compute_one(eng, FLOWS):
            assert ar.capacity_adj >= 0.0


class TestSpecialCases:
    def test_right_turn_bypass(self):
        FLOWS = [[0,200,150,100],[100,0,100,100],[100,100,0,100],[100,100,100,0]]
        arms_tad = [{"name":str(i),"LE4m":3.5,"has_right_turn":i==0} for i in range(4)]
        arms_no  = [{"name":str(i),"LE4m":3.5,"has_right_turn":False} for i in range(4)]
        res_tad = compute_one(make_engine(10,8,0,Milieu.PU,arms_tad), FLOWS)
        res_no  = compute_one(make_engine(10,8,0,Milieu.PU,arms_no),  FLOWS)
        assert res_tad[0].QE == pytest.approx(250.0, abs=ATOL)
        assert res_no [0].QE == pytest.approx(450.0, abs=ATOL)
        assert res_tad[1].QG < res_no[1].QG

    def test_exit_only_arm(self):
        arms_spec = [{"name":"Nord","LE4m":4.0},{"name":"Est","LE4m":4.0,"exit_only":True},
                     {"name":"Sud","LE4m":4.0},{"name":"Ouest","LE4m":4.0}]
        eng = make_engine(10, 8, 0, Milieu.PU, arms_spec)
        FLOWS = [[0,100,100,100],[100,0,100,100],[100,100,0,100],[100,100,100,0]]
        est = compute_one(eng, FLOWS)[1]
        assert est.capacity == 0.0
        assert est.QE == 0.0
        assert est.saturated is False

    def test_saturated_arm(self):
        arms = [{"name": n, "LE4m": 3.0} for n in "ABCD"]
        eng = make_engine(10, 8, 0, Milieu.PU, arms)
        FLOWS = [[0,800,800,800],[800,0,800,800],[800,800,0,800],[800,800,800,0]]
        saturated = [ar for ar in compute_one(eng, FLOWS) if ar.saturated]
        assert len(saturated) > 0
        for ar in saturated:
            assert ar.mean_delay_s == -1.0
            assert ar.mean_queue_veh == -1.0


class TestMiniGiratoire:
    def test_mini_gir_uses_rc_constants(self):
        eng = make_engine(0, 5, 0, Milieu.PU, [{"name":n,"LE4m":3.0} for n in "ABCD"])
        assert eng.Tg  == pytest.approx(_Tg [Milieu.RC], rel=1e-9)
        assert eng.Te  == pytest.approx(_Te [Milieu.RC], rel=1e-9)
        assert eng.Tf1 == pytest.approx(_Tf1[Milieu.RC], rel=1e-9)

    def test_mini_gir_geometry(self):
        eng = make_engine(0, 6, 0.5, Milieu.PU, [{"name":"A","LE4m":2.0}])
        assert eng.RU   == pytest.approx(3.5, abs=1e-9)
        assert eng.LAU  == pytest.approx(3.0, abs=1e-9)
        assert eng.Rext == pytest.approx(6.5, abs=1e-9)


class TestGlobalConsistency:
    FLOWS = [[0,150,200,200],[100,0,200,150],[200,200,0,330],[100,100,130,0]]
    ARMS  = [{"name":n,"LE4m":4.5} for n in "ABCD"]

    def test_total_flow_conserved(self):
        eng = make_engine(10, 8, 0, Milieu.PU, self.ARMS)
        res = compute_one(eng, self.FLOWS)
        total_QE = sum(ar.QE for ar in res)
        total_in = sum(self.FLOWS[i][j] for i in range(4) for j in range(4))
        assert total_QE == pytest.approx(total_in, abs=ATOL)

    def test_rc_pct_consistent(self):
        eng = make_engine(10, 8, 0, Milieu.PU, self.ARMS)
        for ar in compute_one(eng, self.FLOWS):
            if ar.capacity_adj > 0:
                expected = (1.0 - ar.QE/ar.capacity_adj)*100.0
                assert ar.reserve_pct == pytest.approx(expected, abs=ATOL)

    def test_mm1_delay_formula(self):
        eng = make_engine(10, 8, 0, Milieu.PU, self.ARMS)
        for ar in compute_one(eng, self.FLOWS):
            if ar.capacity_adj > ar.QE > 0:
                expected = ar.QE * 3600.0 / (ar.capacity_adj * (ar.capacity_adj - ar.QE))
                assert ar.mean_delay_s == pytest.approx(expected, abs=ATOL)
