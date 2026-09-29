# Changelog

All notable changes to the Lightning AI skills plugin. Versions match `version` in
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) and are tagged `vX.Y.Z` on `main`.

## [1.0.0] - unreleased

First public release, submitted to the Claude directory.

- Seven skills: `lightning-studios`, `lightning-jobs`, `lightning-deployments`,
  `lightning-sandboxes`, `lightning-llm-gateway`, `lightning-artifacts`,
  `lightning-cost-estimation`
- Installable as a Claude Code plugin, through skills.sh, or by copying the folders
- Apache-2.0 license; every skill declares `license` and `compatibility`
- CI validates each skill against the Agent Skills spec and the plugin manifests
