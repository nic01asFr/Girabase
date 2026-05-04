"""
Girabase — Moteur de calcul de capacité des carrefours giratoires
Port Python complet du modèle CERTU/Girabase (VB6, 1998, CETE de l'Ouest)

Modèle : créneaux de Siegloch adapté (Note de calcul CERTU)
  Ci  = (3600/Tf) × exp(-QG/3600 × (Tg - Tf/2))           §2.5.1
  Cvh = Ci × (LEGirabase / 3.5)^Te                         §2.5.2

Sources :
  - BRANCHE.cls    : getCVH(), CalculParamBranche(), KS, TTP
  - GIRATOIRE.cls  : CalculParamGiratoire(), RU/LAU/LEU/LImax/KI/KE
  - GirabaseMain.bas: constantes Tg/Te/Tf1 par milieu
  - strConst.bas   : valeurs numériques
  - PDF Girabase4050 (CEREMA) : plages de validité, interprétation RC
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import IntEnum


# ---------------------------------------------------------------------------
# Constantes de milieu (GirabaseMain.bas)
# ---------------------------------------------------------------------------

class Milieu(IntEnum):
    RC = 0   # Rase Campagne
    PU = 1   # Périurbain
    CV = 2   # Centre-Ville

# [RC, PU, CV]
_Tg  = [4.75, 4.55, 4.40]   # Créneau critique (s)
_Te  = [0.70, 0.80, 0.85]   # Exposant correction largeur d'entrée
_Tf1 = [2.25, 2.05, 1.80]   # Créneau complémentaire de base (s)

COEF_LEU = 1.2   # Coefficient LEU (1.1 en version wallonne)

# Équivalences UVP (PDF p.5)
PCE = {"VL": 1.0, "PL": 2.0, "2R": 0.5, "UVP": 1.0}

# Facteur rampe (§1.3)
RAMP_TF_FACTOR = 1.35

# Espacement moyen en file (m/véhicule) — pour conversion veh → m
VEH_SPACING_M = 6.0


# ---------------------------------------------------------------------------
# Plages de validité géométrique (PDF p.17)
# ---------------------------------------------------------------------------

GEOM_LIMITS = {
    "n_arms":   (3, 8),
    "R":        (0.0, 100.0),
    "Bf":       (0.0, 3.0),
    "LA":       (4.5, 12.0),
    "LE":       (0.0, 12.0),
    "LS":       (0.0, 10.0),
    "pedestrians": (0, 2500),
    "flow_dir": (0.0, 2500.0),
}

# Rayon extérieur selon milieu et type (PDF p.17)
REXT_LIMITS = {
    Milieu.RC: (4.5, 18.0),
    Milieu.PU: (7.5, 12.0),
    Milieu.CV: (7.5, 12.0),
}
REXT_MINI_MAX = 12.0   # Mini-giratoire


# ---------------------------------------------------------------------------
# Seuils d'interprétation RC% (PDF p.19)
# ---------------------------------------------------------------------------

# (seuil_bas, niveau, texte)
RC_THRESHOLDS = [
    (80,  "overdesigned", "Giratoire probablement non justifié"),
    (50,  "oversize",     "Entrée surdimensionnée — envisager réduction"),
    (25,  "ok",           "Bon fonctionnement"),
    (5,   "warning",      "Files d'attente possibles aux hyperpointes"),
    (-999,"critical",     "Saturation sévère"),
]


def rc_level(reserve_pct: float) -> tuple[str, str]:
    """Retourne (niveau, texte) pour une valeur de réserve de capacité."""
    for threshold, level, text in RC_THRESHOLDS:
        if reserve_pct > threshold:
            return level, text
    return "critical", "Saturation sévère"


# ---------------------------------------------------------------------------
# Structures de données — Entrée
# ---------------------------------------------------------------------------

@dataclass
class RoundaboutGeometry:
    """Géométrie globale de l'anneau (GIRATOIRE.cls)."""
    R:      float          # Rayon îlot infranchissable (m) — 0 = mini-giratoire
    LA:     float          # Largeur de l'anneau (m)
    Bf:     float          # Largeur bande franchissable (m)
    milieu: Milieu         # Type de milieu
    n_arms: int = 4        # Nombre de branches (3–8)


@dataclass
class ArmGeometry:
    """Géométrie d'une branche (BRANCHE.cls)."""
    name:      str
    LE4m:      float          # Largeur d'entrée à 4 m du nez d'îlot (m)
    LE15m:     float = 0.0    # Largeur d'entrée à 15 m (évasée) (m)
    LI:        float = 0.0    # Largeur îlot séparateur (m)
    LS:        float = 0.0    # Largeur de sortie (m)
    evasee:    bool  = False  # Entrée évasée → LE = (LE4m+LE15m)/2  §1.2
    has_ramp:  bool  = False  # Rampe > 3% → Tf × 1.35              §1.3
    has_right_turn: bool = False  # Voie directe tourne-à-droite     PDF p.13
    pedestrians_per_hour: int = 0 # Piétons traversant (/h)         §2.5.3
    exit_only: bool  = False  # Sortie seulement (pas d'entrée)


@dataclass
class TrafficPeriod:
    """
    Matrice O-D pour une période de trafic.
    flows[i][j] = débit entrant bras i → sortant bras j (veh/h).
    flows[i][i] = 0 (demi-tours ignorés).
    """
    name:  str
    flows: list[list[float]]


# ---------------------------------------------------------------------------
# Structures de données — Résultats
# ---------------------------------------------------------------------------

@dataclass
class ValidationWarning:
    level:   str   # "error" | "warning" | "info"
    message: str


@dataclass
class ArmResult:
    arm:              str
    QE:               float   # Trafic entrant effectif (veh/h)
    QG:               float   # Trafic giratoire au droit de l'entrée (veh/h)
    capacity:         float   # Capacité sans correction piétons (veh/h)
    capacity_adj:     float   # Capacité après correction piétons (veh/h)
    reserve_pct:      float   # Réserve de capacité RC (%)
    saturated:        bool
    rc_level:         str     # "ok" | "warning" | "critical" | "overdesigned" | "oversize"
    rc_text:          str     # Texte d'interprétation
    LE_retenu:        float   # LEGirabase = min(LEU, LE) utilisé dans le calcul
    KS:               float   # Coefficient gêne sortant §2.2.1
    Tf:               float   # Créneau complémentaire retenu (s)
    TTP:              float   # Temps traversée piéton (s) §2.5.3
    # Temps d'attente et files (M/M/1, régime stationnaire)
    mean_delay_s:     float   # Temps moyen d'attente (s/veh)
    total_delay_veh_h: float  # Perte totale (veh·h/h)
    mean_queue_veh:   float   # Longueur moyenne de file (veh)
    max_queue_veh:    float   # Longueur max estimée ~95e percentile (veh)


@dataclass
class PeriodResult:
    period:             str
    arms:               list[ArmResult]
    max_saturation_pct: float
    saturated_arms:     list[str]


@dataclass
class GirabaseResult:
    geometry:  RoundaboutGeometry
    # Paramètres géométriques dérivés
    RU:    float   # Rayon utile îlot (m)
    LAU:   float   # Largeur utile anneau (m)
    LEU:   float   # Largeur d'entrée utile max (m) §2.1.1
    LImax: float   # Largeur max îlot admissible (m) §2.1.4
    KI:    float   # Coefficient gêne intérieur §2.2.1
    KE:    float   # Coefficient gêne extérieur
    Rext:  float   # Rayon extérieur (m)
    # Résultats
    periods:  list[PeriodResult]
    warnings: list[ValidationWarning]


# ---------------------------------------------------------------------------
# Moteur de calcul principal
# ---------------------------------------------------------------------------

class GirabaseEngine:
    """
    Port Python complet du moteur CERTU/Girabase.
    Toutes les formules sont référencées par §section de la Note de Calcul.
    """

    def __init__(self, geometry: RoundaboutGeometry, arms: list[ArmGeometry]):
        assert geometry.n_arms == len(arms), \
            f"n_arms={geometry.n_arms} mais {len(arms)} bras fournis"

        self.geom = geometry
        self.arms = arms

        m = int(geometry.milieu)
        self.Tg  = _Tg[m]
        self.Te  = _Te[m]
        self.Tf1 = _Tf1[m]

        if geometry.R == 0:
            self.Tg  = _Tg[Milieu.RC]
            self.Te  = _Te[Milieu.RC]
            self.Tf1 = _Tf1[Milieu.RC]

        self._compute_global_params()
        self._compute_arm_params()

    def _compute_global_params(self) -> None:
        R, LA, Bf = self.geom.R, self.geom.LA, self.geom.Bf

        if R == 0:
            self.RU  = 3.5
            self.LAU = LA + Bf - 3.5
        else:
            self.RU  = R + 0.5 * Bf
            self.LAU = LA + 0.5 * Bf

        RU, LAU = self.RU, self.LAU
        self.Rext = R + Bf + LA
        self.LEU = LAU / (COEF_LEU * (1.0 + 1.0 / (2.0 * RU)))
        self.LImax = self.Tg * math.sqrt(RU + LAU / 2.0)
        self.KI = 8.0 / LAU * math.sqrt(20.0 / (RU + LAU))

        if LAU > 8.0:
            self.KE = 1.0 - (RU / (RU + LAU)) ** 2 * (LAU - 8.0) / LAU
        else:
            self.KE = 1.0

        self.KI = min(self.KE, self.KI)

    def _compute_arm_params(self) -> None:
        self._arm_LE:  list[float] = []
        self._arm_LEg: list[float] = []
        self._arm_Tf:  list[float] = []
        self._arm_KS:  list[float] = []
        self._arm_TTP: list[float] = []

        for arm in self.arms:
            LE = (arm.LE4m + arm.LE15m) / 2.0 if arm.evasee else arm.LE4m
            LEg = min(self.LEU, LE)
            Tf = self.Tf1 * RAMP_TF_FACTOR if arm.has_ramp else self.Tf1
            SLB = max(arm.LS - 5.0, 0.0)
            KS  = self.RU / (self.RU + self.LAU) - (arm.LI + 0.5 * SLB) / self.LImax
            KS  = max(0.0, KS)
            TTP = min(arm.LE4m, 4.0)

            self._arm_LE.append(LE)
            self._arm_LEg.append(LEg)
            self._arm_Tf.append(Tf)
            self._arm_KS.append(KS)
            self._arm_TTP.append(TTP)

    def _cvh(self, arm_idx: int, QG: float) -> tuple[float, float]:
        Tf  = self._arm_Tf[arm_idx]
        LEg = self._arm_LEg[arm_idx]
        qg_s = QG / 3600.0
        exposant = -qg_s * (self.Tg - Tf / 2.0)
        Ci = (3600.0 / Tf) * math.exp(exposant)
        return Ci * (LEg / 3.5) ** self.Te, exposant

    def curve_points(self, arm_idx: int, n_points: int = 60) -> tuple[list[float], list[float]]:
        qg_step = 2000.0 / n_points
        xs = [i * qg_step for i in range(n_points + 1)]
        ys = [max(0.0, self._cvh(arm_idx, qg)[0]) for qg in xs]
        return xs, ys

    def _circulating_flows(self, flows: list[list[float]], n: int) -> list[float]:
        QG = [0.0] * n
        for i in range(n):
            QT = 0.0
            for j in range(n):
                if j == i:
                    continue
                for k in range(n):
                    if k == j:
                        continue
                    if _passes_entry(j, i, k, n):
                        QT += flows[j][k]

            QSortant = sum(flows[j][i] for j in range(n))
            KS = self._arm_KS[i]
            if QT + QSortant > 0 and KS > 0:
                QSortantGenant = KS * QSortant * (1.0 - QSortant / (QT + QSortant))
            else:
                QSortantGenant = 0.0

            QG[i] = QT + QSortantGenant
        return QG

    def validate(self) -> list[ValidationWarning]:
        warnings: list[ValidationWarning] = []
        geom = self.geom

        def err(msg):  warnings.append(ValidationWarning("error",   msg))
        def warn(msg): warnings.append(ValidationWarning("warning", msg))
        def info(msg): warnings.append(ValidationWarning("info",    msg))

        n = geom.n_arms
        if not (3 <= n <= 8):
            err(f"Nombre de branches {n} hors plage [3, 8]")
        if geom.R > 100:
            err(f"Rayon d'îlot R={geom.R}m > maximum 100 m")
        if geom.Bf > 3.0:
            err(f"Bande franchissable Bf={geom.Bf}m > maximum 3 m")
        if not (4.5 <= geom.LA <= 12.0):
            err(f"Largeur d'anneau LA={geom.LA}m hors plage [4.5, 12] m")
        if geom.LA > 10.0:
            warn("Anneau > 10 m : risque sécurité (néfaste sauf si ≥ 1 entrée à 3 voies)")

        Rext = self.Rext
        if geom.R == 0:
            if Rext > REXT_MINI_MAX:
                err(f"Mini-giratoire : Rext={Rext:.1f}m > 12 m")
        else:
            rmin, rmax = REXT_LIMITS[geom.milieu]
            if not (rmin <= Rext <= rmax):
                warn(f"Rayon extérieur Rext={Rext:.1f}m hors plage [{rmin}, {rmax}] m "
                     f"pour milieu {geom.milieu.name}")

        if Rext > 15.0 and geom.Bf > 0:
            info(f"Rext={Rext:.1f}m > 15 m : bande franchissable à intégrer au rayon")

        for i, arm in enumerate(self.arms):
            tag = f"Bras «{arm.name}»"
            if arm.LE4m > 12.0:
                err(f"{tag} : LE4m={arm.LE4m}m > 12 m")
            if arm.LS > 10.0:
                warn(f"{tag} : LS={arm.LS}m > 10 m")
            if arm.evasee and arm.LE15m == 0.0:
                warn(f"{tag} : évasée cochée mais LE15m=0")
            if arm.pedestrians_per_hour > 2500:
                err(f"{tag} : piétons {arm.pedestrians_per_hour}/h > 2 500/h")
            if arm.has_right_turn and arm.exit_only:
                warn(f"{tag} : tourne-à-droite et sortie-seulement incompatibles")

        return warnings

    def compute(self, periods: list[TrafficPeriod]) -> GirabaseResult:
        n = self.geom.n_arms
        period_results: list[PeriodResult] = []
        warnings = self.validate()

        for period in periods:
            f = period.flows
            if len(f) != n or any(len(row) != n for row in f):
                raise ValueError(
                    f"Matrice '{period.name}' : taille attendue {n}×{n}"
                )

            eff = [row[:] for row in f]
            for i, arm in enumerate(self.arms):
                if arm.has_right_turn:
                    eff[i][(i + 1) % n] = 0.0

            QE = [sum(eff[i]) for i in range(n)]
            QG = self._circulating_flows(eff, n)

            arm_results: list[ArmResult] = []
            saturated_arms: list[str] = []

            for i, arm in enumerate(self.arms):
                if arm.exit_only:
                    arm_results.append(_empty_result(
                        arm.name, QG[i], self._arm_LEg[i],
                        self._arm_KS[i], self._arm_Tf[i], self._arm_TTP[i]))
                    continue

                qe = QE[i]
                cap, exposant = self._cvh(i, QG[i])

                TTP = self._arm_TTP[i]
                P   = arm.pedestrians_per_hour
                if P > 0 and TTP > 0:
                    Cp = (TTP / 10.0) * (1.0 - math.exp(-P / 360.0)) \
                         * (1800.0 + P) / 2160.0 * math.exp(exposant)
                    Cp = min(1.0, max(0.0, Cp))
                else:
                    Cp = 0.0
                cap_adj = cap * (1.0 - Cp)

                reserve_pct = (1.0 - qe / cap_adj) * 100.0 if cap_adj > 0 else -999.0
                saturated = qe >= cap_adj
                if saturated:
                    saturated_arms.append(arm.name)

                level, text = rc_level(reserve_pct)

                if cap_adj > qe > 0:
                    mean_delay_s      = qe * 3600.0 / (cap_adj * (cap_adj - qe))
                    mean_queue_veh    = qe ** 2 / (cap_adj * (cap_adj - qe))
                    max_queue_veh     = mean_queue_veh + 2.0 * math.sqrt(max(mean_queue_veh, 1e-6))
                    total_delay_veh_h = qe * mean_delay_s / 3600.0
                elif qe == 0:
                    mean_delay_s = total_delay_veh_h = mean_queue_veh = max_queue_veh = 0.0
                else:
                    mean_delay_s = total_delay_veh_h = mean_queue_veh = max_queue_veh = float("inf")

                arm_results.append(ArmResult(
                    arm=arm.name, QE=round(qe, 1), QG=round(QG[i], 1),
                    capacity=round(cap, 1), capacity_adj=round(cap_adj, 1),
                    reserve_pct=round(reserve_pct, 1), saturated=saturated,
                    rc_level=level, rc_text=text,
                    LE_retenu=round(self._arm_LEg[i], 3),
                    KS=round(self._arm_KS[i], 4),
                    Tf=round(self._arm_Tf[i], 3),
                    TTP=round(TTP, 1),
                    mean_delay_s=_fmt_inf(mean_delay_s, 1),
                    total_delay_veh_h=_fmt_inf(total_delay_veh_h, 2),
                    mean_queue_veh=_fmt_inf(mean_queue_veh, 1),
                    max_queue_veh=_fmt_inf(max_queue_veh, 1),
                ))

            max_sat = max(
                (r.QE / r.capacity_adj * 100.0 for r in arm_results if r.capacity_adj > 0),
                default=0.0,
            )
            period_results.append(PeriodResult(
                period=period.name, arms=arm_results,
                max_saturation_pct=round(max_sat, 1),
                saturated_arms=saturated_arms,
            ))

        return GirabaseResult(
            geometry=self.geom,
            RU=round(self.RU, 3), LAU=round(self.LAU, 3),
            LEU=round(self.LEU, 3), LImax=round(self.LImax, 3),
            KI=round(self.KI, 4), KE=round(self.KE, 4),
            Rext=round(self.Rext, 2),
            periods=period_results, warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _passes_entry(origin: int, entry: int, dest: int, n: int) -> bool:
    if origin == dest:
        return False
    d_entry = (entry - origin) % n
    d_dest  = (dest  - origin) % n
    return 0 < d_entry < d_dest


def _fmt_inf(value: float, decimals: int) -> float:
    if math.isinf(value) or math.isnan(value):
        return -1.0
    return round(value, decimals)


def _empty_result(name, QG, LEg, KS, Tf, TTP) -> ArmResult:
    return ArmResult(
        arm=name, QE=0.0, QG=round(QG, 1),
        capacity=0.0, capacity_adj=0.0, reserve_pct=0.0,
        saturated=False, rc_level="ok", rc_text="Sortie uniquement",
        LE_retenu=round(LEg, 3), KS=round(KS, 4), Tf=round(Tf, 3), TTP=round(TTP, 1),
        mean_delay_s=0.0, total_delay_veh_h=0.0, mean_queue_veh=0.0, max_queue_veh=0.0,
    )
