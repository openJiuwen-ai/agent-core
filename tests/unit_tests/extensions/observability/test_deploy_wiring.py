# coding: utf-8

"""Structural checks for the deploy/observability metrics wiring."""

from pathlib import Path

import yaml

DEPLOY_DIR = Path(__file__).resolve().parents[4] / "deploy" / "observability"


def _collector_config() -> dict:
    with (DEPLOY_DIR / "otel-collector-config.yaml").open() as handle:
        return yaml.safe_load(handle)


def _compose_config() -> dict:
    with (DEPLOY_DIR / "docker-compose.yml").open() as handle:
        return yaml.safe_load(handle)


def _prometheus_config() -> dict:
    with (DEPLOY_DIR / "prometheus.yml").open() as handle:
        return yaml.safe_load(handle)


def test_collector_exposes_a_metrics_pipeline() -> None:
    config = _collector_config()
    pipelines = config["service"]["pipelines"]
    assert "metrics" in pipelines
    metrics = pipelines["metrics"]
    assert metrics["receivers"] == ["otlp"]
    assert "prometheus" in metrics["exporters"]
    assert "debug" in metrics["exporters"]


def test_collector_defines_prometheus_exporter() -> None:
    exporters = _collector_config()["exporters"]
    assert exporters["prometheus"]["endpoint"] == "0.0.0.0:8889"


def test_trace_pipeline_keeps_its_existing_shape() -> None:
    traces = _collector_config()["service"]["pipelines"]["traces"]
    assert traces["exporters"] == ["otlphttp/langfuse", "debug"]


def test_compose_exposes_collector_metrics_port() -> None:
    collector = _compose_config()["services"]["otel-collector"]
    assert "8889:8889" in collector["ports"]


def test_compose_adds_a_prometheus_service() -> None:
    services = _compose_config()["services"]
    prometheus = services["prometheus"]
    assert prometheus["image"].startswith("prom/prometheus:")
    assert "9090:9090" in prometheus["ports"]
    mount = prometheus["volumes"][0]
    assert mount.startswith("./prometheus.yml:")
    assert mount.endswith(":ro")


def test_prometheus_scrapes_the_collector() -> None:
    jobs = _prometheus_config()["scrape_configs"]
    assert jobs[0]["job_name"] == "otel-collector"
    targets = jobs[0]["static_configs"][0]["targets"]
    assert targets == ["otel-collector:8889"]
