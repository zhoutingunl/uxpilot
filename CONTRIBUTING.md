# Contributing

Use a feature branch and pull request. Keep task goals separate from implementation details; Explorer should not receive source or deterministic verification rules.

Run Ruff, pytest with branch coverage, and `uv build`. Tests must not require paid APIs or mutate public demos. Use local browser fixtures for interactions and HTTP transport doubles for model failure paths. Keep failures and missing evidence explicit rather than converting them into successful outcomes.

Add adapters for product-specific protocols; keep generic navigation, observation, evidence and report behavior independent of one product. Source references must be checked against real files and supplied line ranges. Never add credentials, user recordings, cloned repositories or private run artifacts to commits.
