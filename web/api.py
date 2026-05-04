"""
Girabase — API FastAPI
Expose le moteur de capacité giratoire CERTU comme service web JSON.
"""

from __future__ import annotations
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
import os

from engine import (
    GirabaseEngine,
    RoundaboutGeometry,
    ArmGeometry,
    TrafficPeriod,
    Milieu,
    rc_level,
    _Tg, _Te, _Tf1,
)
import concurrent.futures
import math

app = FastAPI(
    title="Girabase API",
    description="Calcul de capacité des carrefours giratoires — modèle CERTU/Girabase (Siegloch)",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MILIEU_MAP = {"RC": Milieu.RC, "PU": Milieu.PU, "CV": Milieu.CV}
MILIEU_LABELS = {Milieu.RC: "Rase Campagne", Milieu.PU: "Périurbain", Milieu.CV: "Centre-Ville"}


# ---------------------------------------------------------------------------
# Schémas Pydantic — Requête
# ---------------------------------------------------------------------------

class ArmIn(BaseModel):
    name:      str   = Field(..., examples=["Nord"])
    LE4m:      float = Field(..., ge=0, le=12,   description="Largeur entrée à 4 m (m)")
    LE15m:     float = Field(0.0, ge=0, le=12,   description="Largeur entrée à 15 m (m) — si évasée")
    LI:        float = Field(0.0, ge=0,          description="Largeur îlot séparateur (m)")
    LS:        float = Field(0.0, ge=0, le=10,   description="Largeur sortie (m)")
    evasee:    bool  = Field(False, description="Entrée évasée — LE = (LE4m+LE15m)/2")
    has_ramp:  bool  = Field(False, description="Rampe > 3% → Tf×1.35")
    has_right_turn: bool = Field(False,
        description="Voie directe tourne-à-droite : flux bras_i → bras_(i+1) bypasse l'anneau")
    pedestrians_per_hour: int = Field(0, ge=0, le=2500,
        description="Piétons traversant (/h) — réduit la capacité de l'entrée")
    exit_only: bool  = Field(False, description="Sortie uniquement (pas d'entrée)")


class PeriodIn(BaseModel):
    name:  str               = Field(..., examples=["Heure de pointe matin"])
    flows: list[list[float]] = Field(
        ...,
        description="Matrice O-D [n×n] en veh/h. flows[i][j] = entrée bras i → sortie bras j"
    )


class CapacityRequest(BaseModel):
    R:      float  = Field(..., ge=0, le=100, description="Rayon îlot infranchissable (m). 0 = mini-giratoire")
    LA:     float  = Field(..., ge=1, le=12,  description="Largeur anneau (m)")
    Bf:     float  = Field(0.0, ge=0, le=3,   description="Largeur bande franchissable (m)")
    milieu: str    = Field("PU",              description="Milieu : RC | PU | CV")
    arms:    list[ArmIn]    = Field(..., min_length=3, max_length=8)
    periods: list[PeriodIn] = Field(..., min_length=1)

    @model_validator(mode="after")
    def check_matrix_size(self):
        n = len(self.arms)
        for p in self.periods:
            if len(p.flows) != n or any(len(row) != n for row in p.flows):
                raise ValueError(f"Matrice '{p.name}' : taille attendue {n}×{n}")
        return self

    @model_validator(mode="after")
    def check_milieu(self):
        if self.milieu.upper() not in MILIEU_MAP:
            raise ValueError(f"milieu doit être RC, PU ou CV — reçu : '{self.milieu}'")
        return self


# ---------------------------------------------------------------------------
# Schémas Pydantic — Réponse
# ---------------------------------------------------------------------------

class ValidationWarningOut(BaseModel):
    level:   str
    message: str


class ArmResultOut(BaseModel):
    arm:              str
    QE:               float
    QG:               float
    capacity:         float
    capacity_adj:     float
    reserve_pct:      float
    saturated:        bool
    rc_level:         str
    rc_text:          str
    LE_retenu:        float
    KS:               float
    Tf:               float
    TTP:              float
    mean_delay_s:     float
    total_delay_veh_h: float
    mean_queue_veh:   float
    max_queue_veh:    float


class PeriodResultOut(BaseModel):
    period:             str
    arms:               list[ArmResultOut]
    max_saturation_pct: float
    saturated_arms:     list[str]


class GeomParamsOut(BaseModel):
    RU:    float
    LAU:   float
    LEU:   float
    LImax: float
    KI:    float
    KE:    float
    Rext:  float


class ModelInfoOut(BaseModel):
    source:   str
    milieu:   str
    Tg:       float
    Te:       float
    Tf1:      float
    coefLEU:  float


class CapacityResponse(BaseModel):
    geom_params: GeomParamsOut
    model_info:  ModelInfoOut
    periods:     list[PeriodResultOut]
    warnings:    list[ValidationWarningOut]


# ---------------------------------------------------------------------------
# Schémas supplémentaires — Sensibilité
# ---------------------------------------------------------------------------

SENSITIVITY_PARAMS = {"LE4m", "QE", "R", "LA"}


class SensitivityRequest(BaseModel):
    R:          float          = Field(..., ge=0, le=100)
    LA:         float          = Field(..., ge=1, le=12)
    Bf:         float          = Field(0.0, ge=0, le=3)
    milieu:     str            = Field("PU")
    arms:       list[ArmIn]   = Field(..., min_length=3, max_length=8)
    od_matrix:  list[list[float]] = Field(...)
    period_name: str           = Field("Analyse")
    arm_index:  int            = Field(..., ge=0)
    param:      str            = Field(...)
    values:     list[float]    = Field(..., min_length=2, max_length=20)

    @model_validator(mode="after")
    def check_milieu(self):
        if self.milieu.upper() not in MILIEU_MAP:
            raise ValueError(f"milieu doit être RC, PU ou CV")
        return self

    @model_validator(mode="after")
    def check_param(self):
        if self.param not in SENSITIVITY_PARAMS:
            raise ValueError(f"param doit être parmi {SENSITIVITY_PARAMS}")
        return self

    @model_validator(mode="after")
    def check_arm_index(self):
        if self.arm_index >= len(self.arms):
            raise ValueError(f"arm_index {self.arm_index} hors limites")
        return self

    @model_validator(mode="after")
    def check_matrix(self):
        n = len(self.arms)
        if len(self.od_matrix) != n or any(len(r) != n for r in self.od_matrix):
            raise ValueError(f"od_matrix doit être {n}×{n}")
        return self


class SensitivityPoint(BaseModel):
    value:        float
    capacity:     float
    capacity_adj: float
    reserve_pct:  float
    saturated:    bool
    rc_level:     str
    QG:           float


class SensitivityResponse(BaseModel):
    arm:    str
    param:  str
    points: list[SensitivityPoint]


# ---------------------------------------------------------------------------
# Schémas supplémentaires — Horizons
# ---------------------------------------------------------------------------

class HorizonIn(BaseModel):
    label: str   = Field(..., examples=["H+10"])
    years: float = Field(..., gt=0, le=50)
    rate:  float = Field(..., ge=-0.1, le=0.2)


class GrowthRequest(BaseModel):
    base_matrix: list[list[float]] = Field(...)
    horizons:    list[HorizonIn]   = Field(..., min_length=1, max_length=8)


class HorizonOut(BaseModel):
    label:  str
    years:  float
    rate:   float
    matrix: list[list[float]]


class GrowthResponse(BaseModel):
    base_matrix: list[list[float]]
    horizons:    list[HorizonOut]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _build_arm_geom(a: ArmIn) -> ArmGeometry:
    return ArmGeometry(
        name=a.name, LE4m=a.LE4m, LE15m=a.LE15m,
        LI=a.LI, LS=a.LS, evasee=a.evasee,
        has_ramp=a.has_ramp, has_right_turn=a.has_right_turn,
        pedestrians_per_hour=a.pedestrians_per_hour,
        exit_only=a.exit_only,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/capacite", response_model=CapacityResponse,
          summary="Calcul de capacité giratoire (Siegloch CERTU)")
def compute_capacity(req: CapacityRequest):
    """
    Calcule la capacité de chaque bras d'un carrefour giratoire.
    Modèle Siegloch adapté CERTU (§2.5). Délais et files : M/M/1.
    -1 signifie saturé / infini.
    """
    milieu_key = req.milieu.upper()
    milieu = MILIEU_MAP[milieu_key]

    geom = RoundaboutGeometry(
        R=req.R, LA=req.LA, Bf=req.Bf,
        milieu=milieu, n_arms=len(req.arms),
    )
    arms = [_build_arm_geom(a) for a in req.arms]
    periods = [TrafficPeriod(name=p.name, flows=p.flows) for p in req.periods]

    engine = GirabaseEngine(geom, arms)
    result = engine.compute(periods)

    m = int(milieu)
    return CapacityResponse(
        geom_params=GeomParamsOut(
            RU=result.RU, LAU=result.LAU, LEU=result.LEU,
            LImax=result.LImax, KI=result.KI, KE=result.KE,
            Rext=result.Rext,
        ),
        model_info=ModelInfoOut(
            source="CERTU/Girabase — Note de calcul §2.5",
            milieu=MILIEU_LABELS[milieu],
            Tg=_Tg[m], Te=_Te[m], Tf1=_Tf1[m], coefLEU=1.2,
        ),
        periods=[
            PeriodResultOut(
                period=pr.period,
                max_saturation_pct=pr.max_saturation_pct,
                saturated_arms=pr.saturated_arms,
                arms=[
                    ArmResultOut(
                        arm=ar.arm, QE=ar.QE, QG=ar.QG,
                        capacity=ar.capacity, capacity_adj=ar.capacity_adj,
                        reserve_pct=ar.reserve_pct, saturated=ar.saturated,
                        rc_level=ar.rc_level, rc_text=ar.rc_text,
                        LE_retenu=ar.LE_retenu, KS=ar.KS, Tf=ar.Tf, TTP=ar.TTP,
                        mean_delay_s=ar.mean_delay_s,
                        total_delay_veh_h=ar.total_delay_veh_h,
                        mean_queue_veh=ar.mean_queue_veh,
                        max_queue_veh=ar.max_queue_veh,
                    )
                    for ar in pr.arms
                ],
            )
            for pr in result.periods
        ],
        warnings=[
            ValidationWarningOut(level=w.level, message=w.message)
            for w in result.warnings
        ],
    )


@app.get("/api/milieux", summary="Paramètres des milieux disponibles")
def list_milieux():
    return {
        "RC": {"label": "Rase Campagne",  "Tg": 4.75, "Te": 0.70, "Tf1": 2.25},
        "PU": {"label": "Périurbain",     "Tg": 4.55, "Te": 0.80, "Tf1": 2.05},
        "CV": {"label": "Centre-Ville",   "Tg": 4.40, "Te": 0.85, "Tf1": 1.80},
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "girabase", "version": "2.0.0"}


@app.post("/api/sensitivity", response_model=SensitivityResponse,
          summary="Analyse de sensibilité mono-paramètre")
def compute_sensitivity(req: SensitivityRequest):
    milieu = MILIEU_MAP[req.milieu.upper()]
    base_arms = [_build_arm_geom(a) for a in req.arms]
    arm_name = base_arms[req.arm_index].name

    def _run_one(val: float) -> SensitivityPoint:
        R_v  = val if req.param == "R"  else req.R
        LA_v = val if req.param == "LA" else req.LA
        arms = list(base_arms)

        if req.param == "LE4m":
            a = req.arms[req.arm_index]
            arms[req.arm_index] = ArmGeometry(
                name=a.name, LE4m=val, LE15m=a.LE15m,
                LI=a.LI, LS=a.LS, evasee=a.evasee,
                has_ramp=a.has_ramp, has_right_turn=a.has_right_turn,
                pedestrians_per_hour=a.pedestrians_per_hour,
                exit_only=a.exit_only,
            )

        matrix = [row[:] for row in req.od_matrix]
        if req.param == "QE":
            qe_current = sum(req.od_matrix[req.arm_index])
            if qe_current > 0:
                ratio = val / qe_current
                matrix[req.arm_index] = [v * ratio for v in req.od_matrix[req.arm_index]]

        geom = RoundaboutGeometry(R=R_v, LA=LA_v, Bf=req.Bf, milieu=milieu, n_arms=len(arms))
        period = TrafficPeriod(name=req.period_name, flows=matrix)
        result = GirabaseEngine(geom, arms).compute([period])
        ar = result.periods[0].arms[req.arm_index]

        return SensitivityPoint(
            value=val, capacity=round(ar.capacity, 1),
            capacity_adj=round(ar.capacity_adj, 1),
            reserve_pct=round(ar.reserve_pct, 1),
            saturated=ar.saturated, rc_level=ar.rc_level,
            QG=round(ar.QG, 1),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(req.values), 8)) as ex:
        points = list(ex.map(_run_one, req.values))

    return SensitivityResponse(arm=arm_name, param=req.param, points=points)


@app.post("/api/growth_matrix", response_model=GrowthResponse,
          summary="Projection de la matrice O-D par horizons")
def compute_growth_matrix(req: GrowthRequest):
    n = len(req.base_matrix)
    if any(len(row) != n for row in req.base_matrix):
        raise HTTPException(status_code=422, detail="base_matrix n'est pas carrée")

    horizons_out: list[HorizonOut] = []
    for h in req.horizons:
        factor = (1 + h.rate) ** h.years
        projected = [[round(v * factor) for v in row] for row in req.base_matrix]
        horizons_out.append(HorizonOut(
            label=h.label, years=h.years, rate=h.rate, matrix=projected,
        ))

    return GrowthResponse(base_matrix=req.base_matrix, horizons=horizons_out)


# ---------------------------------------------------------------------------
# Servir le frontend statique
# ---------------------------------------------------------------------------

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
