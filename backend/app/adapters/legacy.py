"""Reuse map for API/. No network calls or legacy imports at service startup."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..contracts import CollectedRecord, ConfigurationRequired
from ..models import Plant, VppMembership, VppPortfolio

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


def sync_koen_plants_to_db(session) -> None:
    """Register the 23 KOEN individual solar generators and 20MW VPP portfolios in DB if missing."""
    from API.koen_collector import KOENSolarCollector

    meta_df = KOENSolarCollector().get_plants_metadata()
    portfolio_specs = {
        "VPP_SOUTH_20MW": ("vpp-south-20mw", "남부권 20MW VPP 포트폴리오", Decimal("20000.000")),
        "VPP_CENTRAL_20MW": ("vpp-central-20mw", "중부·서해권 20MW VPP 포트폴리오", Decimal("20000.000")),
        "VPP_GOHEUNG_63MW": ("vpp-goheung-63mw", "고흥만 수상태양광 VPP 포트폴리오", Decimal("63000.000")),
    }
    portfolio_map: dict[str, int] = {}
    for group_key, (slug, name, target_kw) in portfolio_specs.items():
        existing_p = session.scalar(
            select(VppPortfolio).where(VppPortfolio.slug == slug, VppPortfolio.version == "v1")
        )
        if existing_p is None:
            existing_p = VppPortfolio(
                slug=slug,
                name=name,
                version="v1",
                target_capacity_kw=target_kw,
                is_demo=False,
            )
            session.add(existing_p)
            session.flush()
        portfolio_map[group_key] = existing_p.id

    valid_from = datetime(2022, 1, 1, 0, 0, tzinfo=UTC)
    for _, row in meta_df.iterrows():
        pid = str(row["plant_id"])
        plant = session.get(Plant, pid)
        if plant is None:
            plant = Plant(
                id=pid,
                name=str(row["plant_name"]),
                capacity_kw=Decimal(str(row["capacity_kw"])),
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                smp_zone=str(row["smp_zone"]),
                generation_source="koen_solar",
                is_demo=False,
            )
            session.add(plant)
            session.flush()

        p_id = portfolio_map.get(str(row["vpp_group"]))
        if p_id is not None:
            existing_m = session.scalar(
                select(VppMembership).where(
                    VppMembership.portfolio_id == p_id,
                    VppMembership.plant_id == pid,
                    VppMembership.valid_from == valid_from,
                )
            )
            if existing_m is None:
                session.add(
                    VppMembership(
                        portfolio_id=p_id,
                        plant_id=pid,
                        valid_from=valid_from,
                        valid_to=None,
                    )
                )


class LegacyCollector:
    def __init__(self, api_key: str = "", kma_key: str = ""):
        self.api_key = api_key.strip()
        self.kma_key = kma_key.strip()

    def collect(self, start: datetime, end: datetime) -> list[CollectedRecord]:
        if not self.api_key and not self.kma_key:
            raise ConfigurationRequired("Legacy API keys are not configured; see docs/INTEGRATIONS.md.")

        start_kst = start.astimezone(KST) if start.tzinfo else start.replace(tzinfo=UTC).astimezone(KST)
        end_kst = end.astimezone(KST) if end.tzinfo else end.replace(tzinfo=UTC).astimezone(KST)
        s_str = start_kst.strftime("%Y-%m-%d")
        e_str = end_kst.strftime("%Y-%m-%d")
        now_utc = datetime.now(UTC)
        records: list[CollectedRecord] = []

        if self.api_key:
            from API.koen_collector import KOENSolarCollector
            from API.kpx_collector import KPXSMPCollector

            koen_df = KOENSolarCollector(api_key=self.api_key).collect(start_date=s_str, end_date=e_str)
            for _, row in koen_df.iterrows():
                ts_kst = row["timestamp"].to_pydatetime().replace(tzinfo=KST)
                ts_utc = ts_kst.astimezone(UTC)
                plant_id = str(row["plant_id"])
                records.append(
                    CollectedRecord(
                        source="koen_solar",
                        record_key=f"{plant_id}:{ts_utc.isoformat()}",
                        kind="generation",
                        scope_id=plant_id,
                        observed_at=ts_utc,
                        available_at=min(now_utc, ts_utc + timedelta(hours=1)),
                        values={
                            "generation_kwh": float(row["pv_generation_kwh"]),
                            "capacity_kw": float(row["capacity_kw"]),
                        },
                        is_demo=False,
                    )
                )

            smp_df = KPXSMPCollector(api_key=self.api_key).collect(start_date=s_str, end_date=e_str)
            for _, row in smp_df.iterrows():
                ts_kst = row["timestamp"].to_pydatetime().replace(tzinfo=KST)
                ts_utc = ts_kst.astimezone(UTC)
                zone = str(row["smp_zone"])
                records.append(
                    CollectedRecord(
                        source="kpx_smp",
                        record_key=f"market:{zone}:{ts_utc.isoformat()}",
                        kind="market",
                        scope_id=f"market:{zone}",
                        observed_at=ts_utc,
                        available_at=ts_utc,
                        values={"smp_krw_kwh": float(row["smp_krw_kwh"])},
                        is_demo=False,
                    )
                )

        if self.kma_key:
            from API.kma_collector import KMAObsCollector

            obs_df = KMAObsCollector(api_key=self.kma_key).collect(start_date=s_str, end_date=e_str)
            for _, row in obs_df.iterrows():
                ts_kst = row["timestamp"].to_pydatetime().replace(tzinfo=KST)
                ts_utc = ts_kst.astimezone(UTC)
                reg_code = str(row["region_code"])
                records.append(
                    CollectedRecord(
                        source="kma_asos",
                        record_key=f"region:{reg_code}:{ts_utc.isoformat()}",
                        kind="weather_observation",
                        scope_id=f"region:{reg_code}",
                        observed_at=ts_utc,
                        available_at=min(now_utc, ts_utc + timedelta(hours=1)),
                        values={
                            "temperature_c": None if row.isna()["temp_c"] else float(row["temp_c"]),
                            "humidity_pct": None if row.isna()["humidity_pct"] else float(row["humidity_pct"]),
                            "wind_speed_ms": None if row.isna()["wind_speed_ms"] else float(row["wind_speed_ms"]),
                            "solar_radiation_mj": None if row.isna()["solar_radiation_mj"] else float(row["solar_radiation_mj"]),
                            "cloud_cover_10": None if row.isna()["cloud_cover_10"] else float(row["cloud_cover_10"]),
                        },
                        is_demo=False,
                    )
                )

        return records


class ExistingModelAdapter:
    def predict(self, features, cutoff):
        raise ConfigurationRequired("No model artifact or feature schema exists in the baseline repository.")


class LegacyForecastCollector:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key.strip()

    def collect_issue(self, issued_at: datetime) -> list[CollectedRecord]:
        if not self.api_key:
            raise ConfigurationRequired("KMA forecast API → DB mapping and delayed-publication retries are pending.")

        from API.kma_collector import KMAForecastCollector
        from API.koen_collector import KOEN_PLANT_MASTER

        issued_kst = issued_at.astimezone(KST) if issued_at.tzinfo else issued_at.replace(tzinfo=UTC).astimezone(KST)
        issued_utc = issued_kst.astimezone(UTC)
        base_date = issued_kst.strftime("%Y%m%d")
        base_time = issued_kst.strftime("%H00")

        fcst_collector = KMAForecastCollector(api_key=self.api_key)
        unique_grids: dict[tuple[int, int], list[str]] = {}
        for meta in KOEN_PLANT_MASTER.values():
            grid = (int(meta["nx"]), int(meta["ny"]))
            unique_grids.setdefault(grid, []).append(f"KOEN_{meta['ippt']}")

        records: list[CollectedRecord] = []
        now_utc = datetime.now(UTC)

        for (nx, ny), plant_ids in unique_grids.items():
            items = fcst_collector.fetch_forecast(base_date=base_date, base_time=base_time, nx=nx, ny=ny)
            by_target: dict[str, dict] = {}
            for item in items:
                f_date = str(item.get("fcstDate", "")).strip()
                f_time = str(item.get("fcstTime", "")).strip()
                cat = str(item.get("category", "")).strip()
                val = item.get("fcstValue")
                if len(f_date) == 8 and len(f_time) >= 4 and cat:
                    key = f"{f_date}{f_time[:4]}"
                    by_target.setdefault(key, {})[cat] = val

            for target_key, cats in by_target.items():
                target_kst = datetime.strptime(target_key, "%Y%m%d%H%M").replace(tzinfo=KST)
                target_utc = target_kst.astimezone(UTC)
                for pid in plant_ids:
                    records.append(
                        CollectedRecord(
                            source="kma_vilage_fcst",
                            record_key=f"{pid}:issue:{issued_utc.isoformat()}:target:{target_utc.isoformat()}",
                            kind="weather_forecast",
                            scope_id=pid,
                            observed_at=target_utc,
                            issued_at=issued_utc,
                            available_at=max(issued_utc, now_utc),
                            values=cats,
                            is_demo=False,
                        )
                    )
        return records


class MonthlyTrainer:
    def train(self, snapshot, cutoff, output_dir):
        raise ConfigurationRequired("Training code, data snapshot and validation policy are pending.")

    def validate(self, candidate, holdout_snapshot):
        raise ConfigurationRequired("Chronological validation policy is pending.")


class UnconfiguredSettlementRule:
    version = "unconfigured"

    def calculate(self, prediction, market):
        raise ConfigurationRequired("Settlement rules are pending; no amounts were calculated.")
