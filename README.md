# AEO Monitoring Engine

AEO is an answer-engine monitoring system that tracks the performance and consistency of multiple LLM-backed search engines in production. It continuously samples queries across a fleet of Bedrock models, judges responses for quality and adherence to specifications, and detects regressions through statistical trend analysis. The system is designed to scale with configurable sampling rates and maintains full observability through raw response archiving and detailed diagnostic reports.

See the [design specification](docs/2026-07-03-aeo-monitoring-engine-design.md) and [core engine plan](docs/2026-07-04-aeo-core-engine-plan.md) for full details.
